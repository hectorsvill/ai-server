"""
Docker container monitor.
Uses docker-py (sync) run in a thread executor to avoid blocking asyncio.
Tracks: container status, health, restart count, resource usage, log errors.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import docker
from docker.errors import DockerException

from guardian.core.config import cfg
from guardian.core.database import insert_event, insert_metric
from guardian.core.logger import get_logger

log = get_logger(__name__)

# Rate-limit alerts per container to avoid spam
_last_alert: dict[str, float] = {}
_ALERT_COOLDOWN = 120  # seconds


def _should_alert(key: str) -> bool:
    now = time.time()
    if now - _last_alert.get(key, 0) > _ALERT_COOLDOWN:
        _last_alert[key] = now
        return True
    return False


def _get_docker_client() -> docker.DockerClient:
    return docker.DockerClient(base_url=cfg.docker.socket)


def _collect_sync() -> dict[str, Any]:
    """
    Synchronous collection — runs in thread executor.
    Returns a dict keyed by container name.
    """
    client = _get_docker_client()
    result: dict[str, Any] = {
        "containers": {},
        "disk_usage": {},
        "collected_at": time.time(),
    }

    try:
        containers = client.containers.list(all=True)
    except DockerException as e:
        result["error"] = str(e)
        return result

    for c in containers:
        name = c.name
        attrs = c.attrs or {}
        state = attrs.get("State", {})
        restart_count = attrs.get("RestartCount", 0)

        # Health status (if container has HEALTHCHECK)
        health_status = "none"
        if health := state.get("Health"):
            health_status = health.get("Status", "none")

        # Resource stats (non-streaming, single shot)
        cpu_pct = 0.0
        mem_mb = 0.0
        mem_pct = 0.0
        try:
            stats = c.stats(stream=False)
            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            sys_delta = (
                stats["cpu_stats"]["system_cpu_usage"]
                - stats["precpu_stats"].get("system_cpu_usage", 0)
            )
            n_cpus = stats["cpu_stats"].get("online_cpus") or len(
                stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
            )
            if sys_delta > 0:
                cpu_pct = (cpu_delta / sys_delta) * n_cpus * 100.0

            mem_usage = stats["memory_stats"].get("usage", 0)
            mem_limit = stats["memory_stats"].get("limit", 1)
            mem_mb = round(mem_usage / 1e6, 1)
            mem_pct = round(mem_usage / mem_limit * 100, 1)
        except Exception:
            pass  # container not running / race condition

        # Last 20 lines of logs — extract ERROR/WARN lines
        log_errors: list[str] = []
        try:
            raw_logs = c.logs(tail=50, timestamps=False).decode("utf-8", errors="replace")
            for line in raw_logs.splitlines():
                ll = line.lower()
                if any(kw in ll for kw in ("error", "fatal", "panic", "exception", "critical")):
                    log_errors.append(line[:300])  # cap line length
        except Exception:
            pass

        result["containers"][name] = {
            "id": c.short_id,
            "image": (c.image.tags[0] if c.image and c.image.tags else "unknown"),
            "status": c.status,          # running, exited, paused, restarting, dead
            "health": health_status,
            "restart_count": restart_count,
            "started_at": state.get("StartedAt", ""),
            "cpu_pct": round(cpu_pct, 2),
            "mem_mb": mem_mb,
            "mem_pct": mem_pct,
            "log_errors": log_errors[-10:],  # keep last 10 error lines
        }

    # Docker disk usage summary
    try:
        du = client.df()
        result["disk_usage"] = {
            "images_size_gb": round(
                sum(i.get("Size", 0) for i in du.get("Images", [])) / 1e9, 2
            ),
            "volumes_size_gb": round(
                sum(
                    v.get("UsageData", {}).get("Size", 0)
                    for v in du.get("Volumes", [])
                ) / 1e9,
                2,
            ),
            "containers_size_gb": round(
                sum(c.get("SizeRootFs", 0) for c in du.get("Containers", [])) / 1e9, 2
            ),
        }
    except Exception:
        pass

    client.close()
    return result


async def collect_docker_metrics() -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _collect_sync)


async def _check_container_health(data: dict[str, Any]) -> None:
    """Emit events for containers that are down or in a degraded state."""
    containers = data.get("containers", {})
    thresh = cfg.thresholds

    # Build set of expected containers from config
    expected = {s.name for s in cfg.docker.services}

    for name, info in containers.items():
        status = info["status"]
        health = info["health"]
        restarts = info["restart_count"]
        critical = any(s.name == name and s.critical for s in cfg.docker.services)

        sev = "critical" if critical else "warning"

        # Container not running
        if status not in ("running",):
            key = f"container_down_{name}"
            if _should_alert(key):
                await insert_event(
                    sev, "docker",
                    f"Container Down — {name}",
                    f"Container '{name}' is {status} (restarts: {restarts})",
                )
                log.warning("container_down", name=name, status=status, restarts=restarts)

        # Health check failing
        elif health in ("unhealthy",):
            key = f"container_unhealthy_{name}"
            if _should_alert(key):
                await insert_event(
                    sev, "docker",
                    f"Container Unhealthy — {name}",
                    f"Container '{name}' health check is {health}",
                )

        # Restart storms
        if restarts >= thresh.container_restart_critical:
            key = f"container_restart_critical_{name}"
            if _should_alert(key):
                await insert_event(
                    "critical", "docker",
                    f"Container Restart Storm — {name}",
                    f"Container '{name}' has restarted {restarts} times",
                )
        elif restarts >= thresh.container_restart_warning:
            key = f"container_restart_warning_{name}"
            if _should_alert(key):
                await insert_event(
                    "warning", "docker",
                    f"Container Frequent Restarts — {name}",
                    f"Container '{name}' has restarted {restarts} times",
                )

    # Check for expected containers that are completely absent
    for svc in cfg.docker.services:
        if svc.name not in containers and svc.critical:
            key = f"container_missing_{svc.name}"
            if _should_alert(key):
                await insert_event(
                    "critical", "docker",
                    f"Container Missing — {svc.name}",
                    f"Expected container '{svc.name}' not found in Docker",
                )


async def docker_monitor_loop(stop_event: asyncio.Event) -> None:
    interval = cfg.monitoring.docker_interval_seconds
    log.info("docker_monitor_started", interval=interval)

    while not stop_event.is_set():
        try:
            data = await collect_docker_metrics()
            if "error" not in data:
                await insert_metric("docker", data)
                await _check_container_health(data)
                running = sum(
                    1 for c in data["containers"].values() if c["status"] == "running"
                )
                log.debug(
                    "docker_metrics_collected",
                    total=len(data["containers"]),
                    running=running,
                )
            else:
                log.error("docker_collection_error", error=data["error"])
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("docker_monitor_error", exc_info=exc)

        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.ensure_future(stop_event.wait())),
                timeout=interval,
            )
            break
        except asyncio.TimeoutError:
            pass

    log.info("docker_monitor_stopped")


async def get_container_info(name: str) -> dict | None:
    """Single-shot container lookup for the action layer."""
    data = await collect_docker_metrics()
    return data.get("containers", {}).get(name)
