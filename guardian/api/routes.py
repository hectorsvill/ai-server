"""
FastAPI router — all HTTP endpoints for the AI Guardian service.

Endpoints:
  GET  /health                    — liveness probe
  GET  /status                    — full current state snapshot
  GET  /metrics/latest            — latest raw metric snapshots
  GET  /events                    — recent events (with filters)
  GET  /decisions                 — AI decision history
  GET  /actions                   — action history
  GET  /actions/pending           — actions awaiting approval
  POST /actions/approve/{token}   — approve a pending action
  POST /actions/deny/{token}      — deny a pending action
  GET  /actions/approve/{token}   — same (for browser clickable links in Telegram)
  GET  /actions/deny/{token}      — same
  POST /scan                      — trigger immediate AI analysis cycle
  POST /emergency-stop            — enable/disable emergency stop
  GET  /config                    — view active configuration
  GET  /logs                      — tail recent log lines
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from guardian.actions.executor import approve_action, deny_action
from guardian.api.models import (
    ActionRecord,
    ApproveRequest,
    ConfigView,
    DecisionRecord,
    DockerSnapshot,
    EmergencyStopRequest,
    EventRecord,
    HealthResponse,
    StatusResponse,
    SystemSnapshot,
    TriggerScanResponse,
)
from guardian.core.config import cfg
from guardian.core.database import (
    get_action_by_token,
    get_pending_approvals,
    get_recent_actions,
    get_recent_decisions,
    get_recent_events,
    get_latest_metrics,
)
from guardian.core.logger import get_logger
from guardian.monitors.docker_monitor import collect_docker_metrics
from guardian.monitors.system import collect_system_metrics

log = get_logger(__name__)
router = APIRouter()

_start_time = time.time()

# Shared reference to the scan trigger event (set by main)
_scan_trigger_event: Any = None


def set_scan_trigger(event: Any) -> None:
    global _scan_trigger_event
    _scan_trigger_event = event


# ── Health & Status ────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        service=cfg.service.name,
        uptime_seconds=round(time.time() - _start_time, 1),
        emergency_stop=cfg.safety.emergency_stop,
        dry_run=cfg.safety.dry_run,
    )


@router.get("/status", response_model=StatusResponse)
async def status():
    # Fetch latest stored metrics (avoid blocking with fresh collection)
    sys_rows = await get_latest_metrics("system", limit=1)
    docker_rows = await get_latest_metrics("docker", limit=1)
    unresolved = await get_recent_events(limit=1, unresolved_only=True)
    pending = await get_pending_approvals()

    system_snap: SystemSnapshot | None = None
    if sys_rows:
        d = sys_rows[0]["data"]
        cpu = d.get("cpu", {})
        mem = d.get("memory", {})
        system_snap = SystemSnapshot(
            cpu_pct=cpu.get("percent"),
            ram_pct=mem.get("pct"),
            load_avg_1m=cpu.get("load_avg", {}).get("1m"),
            disk_usage=d.get("disks", {}),
            top_processes=d.get("top_processes", [])[:5],
        )

    docker_snap: DockerSnapshot | None = None
    if docker_rows:
        d = docker_rows[0]["data"]
        containers = []
        for name, info in d.get("containers", {}).items():
            containers.append({
                "name": name,
                "status": info.get("status", "unknown"),
                "health": info.get("health", "none"),
                "restart_count": info.get("restart_count", 0),
                "cpu_pct": info.get("cpu_pct", 0),
                "mem_mb": info.get("mem_mb", 0),
            })
        docker_snap = DockerSnapshot(
            containers=containers,
            disk_usage=d.get("disk_usage", {}),
        )

    return StatusResponse(
        hostname=cfg.server.hostname,
        domain=cfg.server.domain,
        system=system_snap,
        docker=docker_snap,
        unresolved_events=len(unresolved),
        pending_approvals=len(pending),
        emergency_stop=cfg.safety.emergency_stop,
        dry_run=cfg.safety.dry_run,
    )


# ── Metrics ────────────────────────────────────────────────────────────────────

@router.get("/metrics/latest")
async def metrics_latest(
    type: str = Query("system", enum=["system", "docker", "security"]),
    limit: int = Query(1, ge=1, le=20),
):
    rows = await get_latest_metrics(type, limit=limit)
    return {"type": type, "count": len(rows), "metrics": rows}


# ── Events ─────────────────────────────────────────────────────────────────────

@router.get("/events", response_model=list[EventRecord])
async def events(
    limit: int = Query(50, ge=1, le=500),
    severity: str | None = Query(None, enum=["info", "warning", "critical"]),
    unresolved_only: bool = Query(False),
):
    rows = await get_recent_events(limit=limit, severity=severity, unresolved_only=unresolved_only)
    return [
        EventRecord(
            id=r["id"],
            timestamp=r["timestamp"],
            severity=r["severity"],
            category=r["category"],
            title=r["title"],
            description=r["description"],
            resolved=bool(r["resolved"]),
        )
        for r in rows
    ]


# ── Decisions ──────────────────────────────────────────────────────────────────

@router.get("/decisions", response_model=list[DecisionRecord])
async def decisions(limit: int = Query(20, ge=1, le=100)):
    rows = await get_recent_decisions(limit=limit)
    return [DecisionRecord(**r) for r in rows]


# ── Actions ────────────────────────────────────────────────────────────────────

@router.get("/actions/pending")
async def actions_pending():
    return await get_pending_approvals()


@router.get("/actions", response_model=list[ActionRecord])
async def actions(limit: int = Query(50, ge=1, le=500)):
    rows = await get_recent_actions(limit=limit)
    result = []
    for r in rows:
        import json
        params = {}
        try:
            params = json.loads(r.get("parameters") or "{}")
        except Exception:
            pass
        result_val = None
        try:
            result_val = json.loads(r.get("result") or "null")
        except Exception:
            pass
        result.append(ActionRecord(
            id=r["id"],
            timestamp=r["timestamp"],
            action_type=r["action_type"],
            target=params.get("target"),
            risk_level=r["risk_level"],
            status=r["status"],
            dry_run=bool(r["dry_run"]),
            result=result_val,
        ))
    return result


# ── Approval (POST for API clients, GET for browser/Telegram links) ────────────

async def _do_approve(token: str, by: str) -> dict:
    action = await get_action_by_token(token)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found or already resolved")
    if action["status"] not in ("pending",):
        raise HTTPException(status_code=409, detail=f"Action is already {action['status']}")
    ok = await approve_action(token, approved_by=by)
    if not ok:
        raise HTTPException(status_code=410, detail="Approval window expired")
    return {"status": "approved", "action_id": action["id"]}


async def _do_deny(token: str, by: str) -> dict:
    action = await get_action_by_token(token)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found or already resolved")
    ok = await deny_action(token, denied_by=by)
    if not ok:
        raise HTTPException(status_code=410, detail="Approval window expired")
    return {"status": "denied", "action_id": action["id"]}


@router.post("/actions/approve/{token}")
async def approve_post(token: str, body: ApproveRequest = ApproveRequest()):
    return await _do_approve(token, body.approved_by)


@router.get("/actions/approve/{token}")
async def approve_get(token: str):
    """Browser-friendly endpoint for Telegram inline buttons."""
    result = await _do_approve(token, "telegram_link")
    return PlainTextResponse(f"✅ Action {result['action_id']} APPROVED.\nYou can close this tab.")


@router.post("/actions/deny/{token}")
async def deny_post(token: str, body: ApproveRequest = ApproveRequest()):
    return await _do_deny(token, body.approved_by)


@router.get("/actions/deny/{token}")
async def deny_get(token: str):
    result = await _do_deny(token, "telegram_link")
    return PlainTextResponse(f"❌ Action {result['action_id']} DENIED.\nYou can close this tab.")


# ── Manual scan ────────────────────────────────────────────────────────────────

@router.post("/scan", response_model=TriggerScanResponse)
async def trigger_scan():
    """Trigger an immediate AI reasoning cycle (does not wait for completion)."""
    if _scan_trigger_event:
        _scan_trigger_event.set()
        return TriggerScanResponse(status="triggered", message="AI reasoning cycle triggered")
    return TriggerScanResponse(status="unavailable", message="Reasoning loop not running")


# ── Emergency stop ─────────────────────────────────────────────────────────────

@router.post("/emergency-stop")
async def emergency_stop(body: EmergencyStopRequest):
    cfg.safety.emergency_stop = body.stop
    state = "ACTIVATED" if body.stop else "DEACTIVATED"
    log.warning("emergency_stop", state=state, reason=body.reason)
    return {"emergency_stop": body.stop, "state": state, "reason": body.reason}


# ── Config view ────────────────────────────────────────────────────────────────

@router.get("/config", response_model=ConfigView)
async def config_view():
    return ConfigView(
        service_name=cfg.service.name,
        domain=cfg.server.domain,
        dry_run=cfg.safety.dry_run,
        emergency_stop=cfg.safety.emergency_stop,
        ai_enabled=cfg.ai.enabled,
        ai_model=cfg.ai.model,
        monitoring_intervals={
            "system_s": cfg.monitoring.system_interval_seconds,
            "docker_s": cfg.monitoring.docker_interval_seconds,
            "security_s": cfg.monitoring.security_interval_seconds,
            "reasoning_s": cfg.monitoring.reasoning_interval_seconds,
        },
        managed_containers=[s.name for s in cfg.docker.services],
        native_services=[s.name for s in cfg.native_services],
    )


# ── Log tail ───────────────────────────────────────────────────────────────────

@router.get("/logs")
async def tail_logs(lines: int = Query(100, ge=1, le=1000)):
    """Return last N lines from the guardian log file."""
    from pathlib import Path
    log_file = Path(cfg.service.log_dir) / "guardian.log"
    if not log_file.exists():
        return PlainTextResponse("Log file not found")
    try:
        # Read last N lines efficiently
        with open(log_file, "rb") as f:
            # Seek to end, then step back
            f.seek(0, 2)
            file_size = f.tell()
            chunk_size = min(file_size, lines * 300)
            f.seek(max(0, file_size - chunk_size))
            content = f.read().decode("utf-8", errors="replace")
        tail = "\n".join(content.splitlines()[-lines:])
        return PlainTextResponse(tail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
