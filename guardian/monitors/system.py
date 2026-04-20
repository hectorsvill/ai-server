"""
Host system resource monitor.
Collects CPU, RAM, disk, network, load average, and AMD GPU stats every N seconds
and stores snapshots in the database.  Emits events when thresholds are crossed.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import time
from typing import Any

import psutil

from guardian.core.config import cfg
from guardian.core.database import insert_event, insert_metric
from guardian.core.logger import get_logger

log = get_logger(__name__)
_thresh = cfg.thresholds

# ── GPU helpers (reuses logic from tools/rocm-stats.py) ──────────────────────

_ROCM_CMD = [
    "rocm-smi",
    "--showuse", "--showmemuse", "--showtemp",
    "--showpower", "--showmaxpower", "--showclocks",
]


def _parse_gpu0(output: str) -> dict[str, str]:
    """Extract GPU[0] key→value pairs from rocm-smi text output."""
    stats: dict[str, str] = {}
    for line in output.splitlines():
        m = re.match(r"GPU\[0\]\s+:\s+(.+?):\s*(.+)", line)
        if m:
            stats[m.group(1).strip()] = m.group(2).strip()
    return stats


def _find(stats: dict[str, str], *patterns: str) -> str:
    """Return first value whose key contains all patterns (case-insensitive)."""
    for k, v in stats.items():
        kl = k.lower()
        if all(p.lower() in kl for p in patterns):
            return v
    return "N/A"


def _clock_mhz(raw: str) -> str:
    m = re.search(r"\((\d+(?:\.\d+)?)\s*[Mm]hz\)", raw)
    return (m.group(1) + " MHz") if m else raw


def _safe_float(val: str) -> float | None:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def collect_gpu_metrics_sync() -> dict[str, Any]:
    """
    Run rocm-smi and return parsed GPU metrics dict.
    Returns {"available": False} if rocm-smi is missing or fails.
    """
    try:
        result = subprocess.run(
            _ROCM_CMD, capture_output=True, text=True, timeout=5
        )
    except FileNotFoundError:
        return {"available": False, "error": "rocm-smi not found"}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "rocm-smi timed out"}

    if result.returncode != 0:
        return {"available": False, "error": result.stderr.strip()[:200]}

    stats = _parse_gpu0(result.stdout)
    if not stats:
        return {"available": False, "error": "no GPU[0] data in rocm-smi output"}

    gpu_use_raw   = _find(stats, "GPU use")
    vram_raw      = _find(stats, "Memory Allocated", "VRAM")
    temp_edge_raw = _find(stats, "Temperature", "edge")
    temp_junc_raw = _find(stats, "Temperature", "junction")
    power_raw     = _find(stats, "Average Graphics Package Power")
    power_max_raw = _find(stats, "Max Graphics Package Power")
    sclk_raw      = _find(stats, "sclk clock")

    gpu_pct      = _safe_float(gpu_use_raw.rstrip("%"))
    vram_pct     = _safe_float(vram_raw.rstrip("%"))
    temp_edge    = _safe_float(temp_edge_raw)
    temp_junc    = _safe_float(temp_junc_raw)
    power_w      = _safe_float(power_raw)
    power_max_w  = _safe_float(power_max_raw)

    power_pct: float | None = None
    if power_w is not None and power_max_w and power_max_w > 0:
        power_pct = round(power_w / power_max_w * 100, 1)

    return {
        "available": True,
        "gpu_pct":      round(gpu_pct, 1)     if gpu_pct     is not None else None,
        "vram_pct":     round(vram_pct, 1)    if vram_pct    is not None else None,
        "temp_edge_c":  temp_edge,
        "temp_junc_c":  temp_junc,
        "power_w":      power_w,
        "power_max_w":  power_max_w,
        "power_pct":    power_pct,
        "sclk":         _clock_mhz(sclk_raw),
    }

# Track last-seen values to avoid duplicate threshold alerts
_last_alert: dict[str, float] = {}
_ALERT_COOLDOWN = 300  # seconds between same-type alerts


def _should_alert(key: str) -> bool:
    now = time.time()
    if now - _last_alert.get(key, 0) > _ALERT_COOLDOWN:
        _last_alert[key] = now
        return True
    return False


async def collect_system_metrics() -> dict[str, Any]:
    """
    Gather a full system snapshot.  Runs psutil calls in thread pool
    to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()

    def _gather() -> dict[str, Any]:
        cpu_pct = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        load1, load5, load15 = psutil.getloadavg()

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # Root filesystem + any additional mount points
        # Filesystems to skip: snap squashfs mounts are always 100% full by design,
        # tmpfs/devtmpfs are memory-backed and not meaningful for disk alerts.
        _SKIP_FSTYPES = {"squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs",
                         "cgroup", "cgroup2", "pstore", "debugfs", "tracefs", "securityfs",
                         "bpf", "hugetlbfs", "mqueue", "fusectl", "efivarfs"}
        _SKIP_MOUNT_PREFIXES = ("/snap/", "/proc", "/sys", "/dev", "/run/user")

        disks: dict[str, Any] = {}
        for part in psutil.disk_partitions(all=False):
            # Skip snap and pseudo-filesystems
            if part.fstype in _SKIP_FSTYPES:
                continue
            if any(part.mountpoint.startswith(p) for p in _SKIP_MOUNT_PREFIXES):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks[part.mountpoint] = {
                    "total_gb": round(usage.total / 1e9, 2),
                    "used_gb": round(usage.used / 1e9, 2),
                    "free_gb": round(usage.free / 1e9, 2),
                    "pct": usage.percent,
                    "fstype": part.fstype,
                }
            except PermissionError:
                pass

        # Network I/O (delta since boot — cumulative)
        net = psutil.net_io_counters()

        # Top 10 processes by CPU
        procs = []
        for p in sorted(
            psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "username"]),
            key=lambda x: x.info.get("cpu_percent") or 0,
            reverse=True,
        )[:10]:
            procs.append(p.info)

        return {
            "cpu": {
                "percent": cpu_pct,
                "count": cpu_count,
                "load_avg": {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)},
            },
            "memory": {
                "total_gb": round(mem.total / 1e9, 2),
                "used_gb": round(mem.used / 1e9, 2),
                "available_gb": round(mem.available / 1e9, 2),
                "pct": mem.percent,
                "swap_pct": swap.percent,
            },
            "disks": disks,
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
                "errin": net.errin,
                "errout": net.errout,
            },
            "top_processes": procs,
        }

    metrics = await loop.run_in_executor(None, _gather)

    # GPU metrics collected in parallel (separate executor call so rocm-smi
    # blocking doesn't delay the psutil snapshot)
    gpu = await loop.run_in_executor(None, collect_gpu_metrics_sync)
    metrics["gpu"] = gpu

    return metrics


async def _check_thresholds(m: dict[str, Any]) -> None:
    """Compare latest snapshot against config thresholds and emit events."""
    cpu = m["cpu"]["percent"]
    ram = m["memory"]["pct"]
    load1 = m["cpu"]["load_avg"]["1m"]

    if cpu >= _thresh.cpu_critical_pct and _should_alert("cpu_critical"):
        await insert_event("critical", "system", "CPU Critical",
                           f"CPU usage at {cpu:.1f}% (threshold: {_thresh.cpu_critical_pct}%)")
    elif cpu >= _thresh.cpu_warning_pct and _should_alert("cpu_warning"):
        await insert_event("warning", "system", "CPU High",
                           f"CPU usage at {cpu:.1f}% (threshold: {_thresh.cpu_warning_pct}%)")

    if ram >= _thresh.ram_critical_pct and _should_alert("ram_critical"):
        await insert_event("critical", "system", "RAM Critical",
                           f"RAM usage at {ram:.1f}% (threshold: {_thresh.ram_critical_pct}%)")
    elif ram >= _thresh.ram_warning_pct and _should_alert("ram_warning"):
        await insert_event("warning", "system", "RAM High",
                           f"RAM usage at {ram:.1f}% (threshold: {_thresh.ram_warning_pct}%)")

    if load1 >= _thresh.load_avg_critical and _should_alert("load_critical"):
        await insert_event("critical", "system", "Load Average Critical",
                           f"1-min load avg {load1:.2f} (threshold: {_thresh.load_avg_critical})")
    elif load1 >= _thresh.load_avg_warning and _should_alert("load_warning"):
        await insert_event("warning", "system", "Load Average High",
                           f"1-min load avg {load1:.2f} (threshold: {_thresh.load_avg_warning})")

    # GPU thresholds (RX 7900 GRE)
    gpu = m.get("gpu", {})
    if gpu.get("available"):
        gpu_pct     = gpu.get("gpu_pct")
        vram_pct    = gpu.get("vram_pct")
        temp_junc   = gpu.get("temp_junc_c")
        power_pct   = gpu.get("power_pct")

        if gpu_pct is not None:
            if gpu_pct >= _thresh.gpu_use_critical_pct and _should_alert("gpu_use_critical"):
                await insert_event("critical", "system", "GPU Usage Critical",
                                   f"GPU utilisation at {gpu_pct}% (threshold: {_thresh.gpu_use_critical_pct}%)")
            elif gpu_pct >= _thresh.gpu_use_warning_pct and _should_alert("gpu_use_warning"):
                await insert_event("warning", "system", "GPU Usage High",
                                   f"GPU utilisation at {gpu_pct}% (threshold: {_thresh.gpu_use_warning_pct}%)")

        if vram_pct is not None:
            if vram_pct >= _thresh.gpu_vram_critical_pct and _should_alert("vram_critical"):
                await insert_event("critical", "system", "GPU VRAM Critical",
                                   f"VRAM usage at {vram_pct}% (threshold: {_thresh.gpu_vram_critical_pct}%)")
            elif vram_pct >= _thresh.gpu_vram_warning_pct and _should_alert("vram_warning"):
                await insert_event("warning", "system", "GPU VRAM High",
                                   f"VRAM usage at {vram_pct}% (threshold: {_thresh.gpu_vram_warning_pct}%)")

        if temp_junc is not None:
            if temp_junc >= _thresh.gpu_temp_critical_c and _should_alert("gpu_temp_critical"):
                await insert_event("critical", "system", "GPU Temperature Critical",
                                   f"GPU junction temp at {temp_junc}°C (threshold: {_thresh.gpu_temp_critical_c}°C)")
            elif temp_junc >= _thresh.gpu_temp_warning_c and _should_alert("gpu_temp_warning"):
                await insert_event("warning", "system", "GPU Temperature High",
                                   f"GPU junction temp at {temp_junc}°C (threshold: {_thresh.gpu_temp_warning_c}°C)")

    # Check every disk partition
    for mount, disk_info in m["disks"].items():
        pct = disk_info["pct"]
        key_crit = f"disk_critical_{mount}"
        key_warn = f"disk_warning_{mount}"
        if pct >= _thresh.disk_critical_pct and _should_alert(key_crit):
            await insert_event(
                "critical", "system", f"Disk Critical — {mount}",
                f"Disk usage at {pct:.1f}% on {mount} "
                f"({disk_info['used_gb']}GB / {disk_info['total_gb']}GB used)",
            )
        elif pct >= _thresh.disk_warning_pct and _should_alert(key_warn):
            await insert_event(
                "warning", "system", f"Disk High — {mount}",
                f"Disk usage at {pct:.1f}% on {mount} "
                f"({disk_info['used_gb']}GB / {disk_info['total_gb']}GB used)",
            )


async def system_monitor_loop(stop_event: asyncio.Event) -> None:
    """
    Main monitoring loop.  Runs until stop_event is set.
    Collects metrics every system_interval_seconds.
    """
    interval = cfg.monitoring.system_interval_seconds
    log.info("system_monitor_started", interval=interval)

    while not stop_event.is_set():
        try:
            metrics = await collect_system_metrics()
            await insert_metric("system", metrics)
            await _check_thresholds(metrics)
            log.debug(
                "system_metrics_collected",
                cpu=metrics["cpu"]["percent"],
                ram=metrics["memory"]["pct"],
                load1=metrics["cpu"]["load_avg"]["1m"],
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("system_monitor_error", exc_info=exc)

        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.ensure_future(stop_event.wait())),
                timeout=interval,
            )
            break  # stop_event fired
        except asyncio.TimeoutError:
            pass  # normal — loop again

    log.info("system_monitor_stopped")
