"""
Security monitor.
Parses auth.log, UFW logs, and scans running processes for threats.
Tracks SSH brute-force attempts, known bad process names, and
suspicious Docker activity.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import psutil

from guardian.core.config import cfg
from guardian.core.database import insert_event, insert_metric
from guardian.core.logger import get_logger

log = get_logger(__name__)

# ── State tracking ─────────────────────────────────────────────────────────────
_ssh_failures: dict[str, list[float]] = defaultdict(list)  # ip → [timestamps]
_banned_ips: dict[str, float] = {}                          # ip → ban_time
_last_alert: dict[str, float] = {}
_ALERT_COOLDOWN = 120


def _should_alert(key: str) -> bool:
    now = time.time()
    if now - _last_alert.get(key, 0) > _ALERT_COOLDOWN:
        _last_alert[key] = now
        return True
    return False


# ── SSH log parsing ───────────────────────────────────────────────────────────
# Patterns for common auth.log entries
_SSH_FAIL_RE = re.compile(
    r"Failed (?:password|publickey) for .+ from (\d+\.\d+\.\d+\.\d+)"
)
_SSH_INVALID_RE = re.compile(
    r"Invalid user \S+ from (\d+\.\d+\.\d+\.\d+)"
)
_SSH_SUCCESS_RE = re.compile(
    r"Accepted (?:password|publickey) for (\S+) from (\d+\.\d+\.\d+\.\d+)"
)
_SSH_DISCONNECT_RE = re.compile(
    r"Disconnected from (?:user |authenticating user )?(\S+) (\d+\.\d+\.\d+\.\d+)"
)

_AUTH_LOG_POSITION = 0  # file offset to track where we left off


def _is_whitelisted(ip: str) -> bool:
    """Check if an IP is in the whitelist (supports CIDR notation)."""
    ban_cfg = cfg.security.auto_ban_ssh
    for entry in ban_cfg.whitelist_ips:
        try:
            if "/" in entry:
                if ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip == entry:
                return True
        except ValueError:
            pass
    return False


async def _parse_auth_log() -> dict[str, Any]:
    """
    Read new lines from auth.log since last check.
    Returns stats: failures_by_ip, successes, new_bans.
    """
    global _AUTH_LOG_POSITION

    auth_log = Path(cfg.security.auth_log)
    if not auth_log.exists():
        return {"failures": {}, "successes": [], "error": "auth.log not found"}

    new_failures: dict[str, int] = defaultdict(int)
    new_connections: list[dict] = []
    new_disconnections: list[dict] = []
    window = 600  # 10-minute window for brute-force detection
    now = time.time()

    try:
        with open(auth_log, "r", errors="replace") as f:
            f.seek(_AUTH_LOG_POSITION)
            lines = f.readlines()
            _AUTH_LOG_POSITION = f.tell()
    except (PermissionError, OSError) as e:
        return {"failures": {}, "connections": [], "disconnections": [], "error": str(e)}

    for line in lines:
        if m := _SSH_FAIL_RE.search(line):
            ip = m.group(1)
            _ssh_failures[ip].append(now)
            new_failures[ip] += 1
        elif m := _SSH_INVALID_RE.search(line):
            ip = m.group(1)
            _ssh_failures[ip].append(now)
            new_failures[ip] += 1
        elif m := _SSH_SUCCESS_RE.search(line):
            new_connections.append({"user": m.group(1), "ip": m.group(2)})
        elif m := _SSH_DISCONNECT_RE.search(line):
            new_disconnections.append({"user": m.group(1), "ip": m.group(2)})

    # Prune old entries outside window
    for ip in list(_ssh_failures.keys()):
        _ssh_failures[ip] = [t for t in _ssh_failures[ip] if now - t <= window]
        if not _ssh_failures[ip]:
            del _ssh_failures[ip]

    return {
        "failures_by_ip": dict(new_failures),
        "recent_failures": {
            ip: len(times) for ip, times in _ssh_failures.items()
        },
        "connections": new_connections,
        "disconnections": new_disconnections,
    }


async def _check_brute_force(auth_data: dict[str, Any]) -> list[str]:
    """Return list of IPs that crossed the brute-force threshold."""
    ban_cfg = cfg.security.auto_ban_ssh
    to_ban: list[str] = []

    for ip, count in auth_data.get("recent_failures", {}).items():
        if _is_whitelisted(ip):
            continue
        thresh = cfg.thresholds

        if count >= ban_cfg.threshold:
            key = f"ssh_critical_{ip}"
            if _should_alert(key):
                await insert_event(
                    "critical", "security",
                    f"SSH Brute Force — {ip}",
                    f"{count} failed SSH attempts in 10 min from {ip}",
                )
                log.warning("ssh_brute_force", ip=ip, attempts=count)
                to_ban.append(ip)
        elif count >= thresh.ssh_failure_critical:
            key = f"ssh_high_{ip}"
            if _should_alert(key):
                await insert_event(
                    "warning", "security",
                    f"SSH Excessive Failures — {ip}",
                    f"{count} failed SSH attempts from {ip}",
                )

    return to_ban


# ── Process scanning ──────────────────────────────────────────────────────────

def _scan_processes_sync() -> list[dict]:
    """Return list of suspicious processes (run in thread executor)."""
    found = []
    patterns = cfg.security.suspicious_process_patterns

    for proc in psutil.process_iter(["pid", "name", "cmdline", "username", "cpu_percent"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        for pattern in patterns:
            if pattern in name or pattern in cmdline:
                found.append({
                    "pid": proc.info.get("pid"),
                    "name": proc.info.get("name"),
                    "cmdline": cmdline[:200],
                    "username": proc.info.get("username"),
                    "cpu_pct": proc.info.get("cpu_percent", 0),
                    "matched_pattern": pattern,
                })
                break

    return found


# ── UFW log parsing ───────────────────────────────────────────────────────────
_UFW_BLOCK_RE = re.compile(r"\[UFW BLOCK\].+SRC=(\d+\.\d+\.\d+\.\d+).+DPT=(\d+)")
_UFW_LOG_POSITION = 0


async def _parse_ufw_log() -> dict[str, Any]:
    global _UFW_LOG_POSITION

    ufw_log = Path(cfg.security.ufw_log)
    if not ufw_log.exists():
        return {"blocks": []}

    blocks: list[dict] = []
    port_hits: dict[int, int] = defaultdict(int)

    try:
        with open(ufw_log, "r", errors="replace") as f:
            f.seek(_UFW_LOG_POSITION)
            lines = f.readlines()
            _UFW_LOG_POSITION = f.tell()
    except (PermissionError, OSError):
        return {"blocks": []}

    for line in lines[-500:]:  # cap to last 500 new lines
        if m := _UFW_BLOCK_RE.search(line):
            src_ip, dpt = m.group(1), int(m.group(2))
            port_hits[dpt] += 1
            # Only record hits on private service ports
            if dpt in cfg.security.private_ports:
                blocks.append({"src": src_ip, "dpt": dpt})

    # Alert on private port probing
    for port, count in port_hits.items():
        if port in cfg.security.private_ports and count >= 5:
            key = f"ufw_private_port_{port}"
            if _should_alert(key):
                await insert_event(
                    "warning", "security",
                    f"Firewall: Private Port Probed — :{port}",
                    f"UFW blocked {count} attempts on private port {port}",
                )

    return {"blocks": blocks[:50], "port_hits": dict(port_hits)}


# ── Open port audit ───────────────────────────────────────────────────────────

def _check_exposed_ports_sync() -> list[dict]:
    """Find ports listening on 0.0.0.0 that should be private."""
    exposed = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != "LISTEN":
            continue
        addr = conn.laddr
        if addr.ip in ("0.0.0.0", "::") and addr.port in cfg.security.private_ports:
            try:
                proc_name = psutil.Process(conn.pid).name() if conn.pid else "unknown"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                proc_name = "unknown"
            exposed.append({"port": addr.port, "pid": conn.pid, "process": proc_name})
    return exposed


# ── Main loop ─────────────────────────────────────────────────────────────────

async def collect_security_metrics(ssh_event_callback: Any = None) -> dict[str, Any]:
    loop = asyncio.get_event_loop()

    auth_data = await _parse_auth_log()
    to_ban = await _check_brute_force(auth_data)

    if ssh_event_callback:
        for conn in auth_data.get("connections", []):
            try:
                await ssh_event_callback("connect", conn["user"], conn["ip"])
            except Exception as exc:
                log.error("ssh_event_callback_error", exc_info=exc)
        for disc in auth_data.get("disconnections", []):
            try:
                await ssh_event_callback("disconnect", disc["user"], disc["ip"])
            except Exception as exc:
                log.error("ssh_event_callback_error", exc_info=exc)
    ufw_data = await _parse_ufw_log()

    susp_procs = await loop.run_in_executor(None, _scan_processes_sync)
    for p in susp_procs:
        key = f"suspicious_proc_{p['pid']}"
        if _should_alert(key):
            await insert_event(
                "critical", "security",
                f"Suspicious Process — {p['name']}",
                f"Process matching pattern '{p['matched_pattern']}': "
                f"pid={p['pid']} user={p['username']} cmd={p['cmdline'][:100]}",
            )
            log.warning("suspicious_process", **p)

    exposed_ports = await loop.run_in_executor(None, _check_exposed_ports_sync)
    for ep in exposed_ports:
        key = f"exposed_port_{ep['port']}"
        if _should_alert(key):
            await insert_event(
                "warning", "security",
                f"Private Port Exposed — :{ep['port']}",
                f"Port {ep['port']} is listening on 0.0.0.0 (should be localhost-only). "
                f"Process: {ep['process']} (pid {ep['pid']})",
            )

    return {
        "auth_log": auth_data,
        "ufw": ufw_data,
        "suspicious_processes": susp_procs,
        "exposed_private_ports": exposed_ports,
        "bans_requested": to_ban,
    }


async def security_monitor_loop(
    stop_event: asyncio.Event,
    ban_callback: Any = None,
    ssh_event_callback: Any = None,
) -> None:
    """
    Main security loop.
    ban_callback(ip: str) is called when an IP exceeds brute-force threshold.
    ssh_event_callback(event_type, user, ip) is called on SSH connect/disconnect.
    """
    interval = cfg.monitoring.security_interval_seconds
    log.info("security_monitor_started", interval=interval)

    while not stop_event.is_set():
        try:
            data = await collect_security_metrics(ssh_event_callback=ssh_event_callback)
            await insert_metric("security", data)

            # Trigger ban callback for detected brute-force IPs
            if ban_callback and data.get("bans_requested"):
                for ip in data["bans_requested"]:
                    await ban_callback(ip)

            log.debug(
                "security_metrics_collected",
                ssh_failure_ips=len(data["auth_log"].get("recent_failures", {})),
                suspicious_procs=len(data["suspicious_processes"]),
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("security_monitor_error", exc_info=exc)

        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.ensure_future(stop_event.wait())),
                timeout=interval,
            )
            break
        except asyncio.TimeoutError:
            pass

    log.info("security_monitor_stopped")
