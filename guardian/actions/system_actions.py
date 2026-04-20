"""
Host system actions: IP banning, disk cleanup, security updates, service restarts.
All destructive operations require human approval (enforced at executor level).
"""
from __future__ import annotations

import asyncio
import shlex
import subprocess
from pathlib import Path
from typing import Any

from guardian.core.config import cfg
from guardian.core.logger import get_logger

log = get_logger(__name__)


async def _run_cmd(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Run a shell command asynchronously, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Command timed out: {' '.join(cmd)}")


class SystemActions:

    # ── IP Banning (UFW) ──────────────────────────────────────────────────────

    async def ban_ip(self, ip: str, duration_minutes: int = 60) -> dict:
        """
        Block an IP address using UFW.
        Schedules automatic unban after duration_minutes.
        """
        # Validate IP format to prevent injection
        import ipaddress
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError(f"Invalid IP address: {ip!r}")

        log.warning("system_ban_ip", ip=ip, duration_min=duration_minutes)

        # Add UFW deny rule
        rc, stdout, stderr = await _run_cmd(["ufw", "deny", "from", ip, "to", "any"])
        if rc != 0:
            raise RuntimeError(f"UFW deny failed: {stderr[:300]}")

        # Schedule removal after duration
        asyncio.create_task(_schedule_unban(ip, duration_minutes))

        return {
            "action": "ip_banned",
            "ip": ip,
            "duration_minutes": duration_minutes,
            "ufw_output": stdout.strip(),
        }

    async def unban_ip(self, ip: str) -> dict:
        """Remove a UFW deny rule for an IP."""
        import ipaddress
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError(f"Invalid IP address: {ip!r}")

        log.info("system_unban_ip", ip=ip)
        rc, stdout, stderr = await _run_cmd(
            ["ufw", "--force", "delete", "deny", "from", ip, "to", "any"]
        )
        return {
            "action": "ip_unbanned",
            "ip": ip,
            "success": rc == 0,
            "ufw_output": stdout.strip(),
        }

    # ── Service Management ─────────────────────────────────────────────────────

    async def restart_service(self, unit_name: str) -> dict:
        """Restart a systemd unit."""
        # Whitelist allowed units to prevent abuse
        allowed_units = {svc.systemd_unit for svc in cfg.native_services}
        if unit_name not in allowed_units:
            raise ValueError(
                f"Unit '{unit_name}' is not in the managed services list. "
                f"Allowed: {allowed_units}"
            )

        log.warning("system_restart_service", unit=unit_name)
        rc, stdout, stderr = await _run_cmd(["systemctl", "restart", unit_name])
        if rc != 0:
            raise RuntimeError(f"systemctl restart {unit_name} failed: {stderr[:300]}")

        return {"action": "service_restarted", "unit": unit_name}

    async def get_service_status(self, unit_name: str) -> dict:
        """Get systemd unit status."""
        rc, stdout, stderr = await _run_cmd(["systemctl", "is-active", unit_name])
        return {
            "unit": unit_name,
            "active": stdout.strip() == "active",
            "status": stdout.strip(),
        }

    async def check_all_native_services(self) -> list[dict]:
        """Check health of all configured native services."""
        results = []
        for svc in cfg.native_services:
            status = await self.get_service_status(svc.systemd_unit)
            results.append({
                "name": svc.name,
                "unit": svc.systemd_unit,
                "active": status["active"],
                "critical": svc.critical,
            })
        return results

    # ── Disk Cleanup ──────────────────────────────────────────────────────────

    async def clean_disk_space(self) -> dict:
        """
        Safe disk cleanup:
        1. Vacuum systemd journal to N days
        2. Clean apt cache
        3. Remove old log files
        Returns summary of space reclaimed.
        """
        log.info("system_clean_disk_space")
        results: list[str] = []
        total_freed_mb = 0.0

        # 1. Journal vacuum
        days = cfg.maintenance.disk_cleanup.clean_journal_days
        rc, stdout, stderr = await _run_cmd(
            ["journalctl", "--vacuum-time", f"{days}d"], timeout=60
        )
        if rc == 0:
            # Parse "Freed X.XM of archived journals"
            import re
            m = re.search(r"Freed ([\d.]+)([KMG])", stdout + stderr)
            if m:
                val, unit = float(m.group(1)), m.group(2)
                mb = val * {"K": 0.001, "M": 1, "G": 1024}.get(unit, 1)
                total_freed_mb += mb
                results.append(f"journal: freed {val}{unit}")

        # 2. apt cache clean
        rc, stdout, stderr = await _run_cmd(["apt-get", "clean"], timeout=60)
        if rc == 0:
            results.append("apt: cache cleaned")

        # 3. Remove old guardian log files (already rotated, delete extras)
        log_dir = Path(cfg.service.log_dir)
        for old_log in log_dir.glob("guardian.log.*"):
            try:
                size_mb = old_log.stat().st_size / 1e6
                old_log.unlink()
                total_freed_mb += size_mb
                results.append(f"removed old log: {old_log.name}")
            except OSError:
                pass

        log.info("system_disk_cleanup_complete", freed_mb=round(total_freed_mb, 1))
        return {
            "action": "disk_cleanup",
            "freed_mb": round(total_freed_mb, 1),
            "steps": results,
        }

    # ── Security Updates ──────────────────────────────────────────────────────

    async def run_security_update(self) -> dict:
        """
        Apply only security-related apt updates.
        This is a MEDIUM-RISK action that requires approval by default.
        """
        log.warning("system_security_update_starting")

        # Update package lists
        rc, stdout, stderr = await _run_cmd(["apt-get", "update", "-qq"], timeout=120)
        if rc != 0:
            raise RuntimeError(f"apt-get update failed: {stderr[:300]}")

        # Dry-run to see what would be upgraded
        rc, stdout, _ = await _run_cmd(
            ["apt-get", "--dry-run", "upgrade", "-y",
             "-o", "APT::Get::Show-Upgraded=true"],
            timeout=60,
        )
        dry_output = stdout[:1000]

        # Apply only security updates via unattended-upgrades
        rc, stdout, stderr = await _run_cmd(
            ["unattended-upgrades", "--minimal_upgrade_steps", "-v"], timeout=300
        )
        success = rc == 0

        log.warning(
            "system_security_update_complete",
            success=success,
            output=stdout[-500:],
        )
        return {
            "action": "security_update",
            "success": success,
            "dry_run_preview": dry_output,
            "update_output": stdout[-500:],
        }

    # ── Port / firewall audit ─────────────────────────────────────────────────

    async def get_ufw_status(self) -> dict:
        """Return current UFW rules."""
        rc, stdout, stderr = await _run_cmd(["ufw", "status", "verbose"])
        return {"raw": stdout[:2000], "rc": rc}

    async def audit_open_ports(self) -> list[dict]:
        """
        Use ss to list all listening ports and flag unexpected ones.
        """
        rc, stdout, stderr = await _run_cmd(["ss", "-tlnp"])
        if rc != 0:
            return []

        ports = []
        for line in stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[3]
            ports.append({"local": local, "raw": line})
        return ports


async def _schedule_unban(ip: str, delay_minutes: int) -> None:
    """Background task to remove a UFW ban after delay_minutes."""
    await asyncio.sleep(delay_minutes * 60)
    try:
        actions = SystemActions()
        await actions.unban_ip(ip)
        log.info("auto_unban_complete", ip=ip)
    except Exception as e:
        log.error("auto_unban_failed", ip=ip, error=str(e))
