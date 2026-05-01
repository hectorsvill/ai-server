"""Tests for guardian.api.routes — all HTTP endpoints via ASGI test client."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app():
    from guardian.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest_asyncio.fixture
async def api(db):
    async with AsyncClient(
        transport=ASGITransport(app=_make_app()),
        base_url="http://test",
    ) as client:
        yield client


# ── /health ───────────────────────────────────────────────────────────────────

async def test_health_returns_ok(api):
    resp = await api.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "emergency_stop" in data
    assert "dry_run" in data


# ── /status ───────────────────────────────────────────────────────────────────

async def test_status_structure(api):
    resp = await api.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "hostname" in data
    assert "emergency_stop" in data
    assert "pending_approvals" in data


async def test_status_reflects_emergency_stop(api, monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", True)
    resp = await api.get("/status")
    assert resp.json()["emergency_stop"] is True


# ── /events ───────────────────────────────────────────────────────────────────

async def test_events_empty(api):
    resp = await api.get("/events")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_events_returns_inserted(api, db):
    from guardian.core.database import insert_event

    await insert_event("critical", "security", "Test Alert", "desc")
    resp = await api.get("/events")
    assert resp.status_code == 200
    titles = [e["title"] for e in resp.json()]
    assert "Test Alert" in titles


async def test_events_severity_filter(api, db):
    from guardian.core.database import insert_event

    await insert_event("critical", "security", "Crit", "desc")
    await insert_event("info", "docker", "Info", "desc")

    resp = await api.get("/events?severity=critical")
    assert all(e["severity"] == "critical" for e in resp.json())


# ── /actions ──────────────────────────────────────────────────────────────────

async def test_actions_empty(api):
    resp = await api.get("/actions")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_actions_pending_list(api, db):
    from guardian.core.database import insert_action

    await insert_action(None, "ban_ip", {"target": "1.2.3.4"}, "critical", "pending", approval_token="p1")

    resp = await api.get("/actions/pending")
    assert resp.status_code == 200
    pending = resp.json()
    assert any(r["approval_token"] == "p1" for r in pending)


# ── /actions/approve ──────────────────────────────────────────────────────────

async def test_approve_post_success(api, db):
    from guardian.core.database import insert_action
    import guardian.actions.executor as exc

    token = "approve-api-tok"
    event = asyncio.Event()
    exc._pending[token] = event

    await insert_action(None, "alert_only", {}, "critical", "pending", approval_token=token)

    resp = await api.post(f"/actions/approve/{token}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert event.is_set()


async def test_approve_post_not_found(api, db):
    resp = await api.post("/actions/approve/nonexistent-token")
    assert resp.status_code == 404


async def test_approve_post_already_completed(api, db):
    from guardian.core.database import insert_action

    token = "already-done"
    await insert_action(None, "alert_only", {}, "low", "completed", approval_token=token)

    resp = await api.post(f"/actions/approve/{token}")
    assert resp.status_code == 409


async def test_approve_post_window_expired(api, db):
    """Token in DB but not in _pending → window expired."""
    from guardian.core.database import insert_action

    token = "expired-window"
    await insert_action(None, "ban_ip", {"target": "9.9.9.9"}, "critical", "pending", approval_token=token)
    # Do NOT register token in _pending

    resp = await api.post(f"/actions/approve/{token}")
    assert resp.status_code == 410


async def test_approve_get_returns_plain_text(api, db):
    from guardian.core.database import insert_action
    import guardian.actions.executor as exc

    token = "get-approve-tok"
    event = asyncio.Event()
    exc._pending[token] = event
    await insert_action(None, "alert_only", {}, "critical", "pending", approval_token=token)

    resp = await api.get(f"/actions/approve/{token}")
    assert resp.status_code == 200
    assert "APPROVED" in resp.text


# ── /actions/deny ────────────────────────────────────────────────────────────

async def test_deny_post_success(api, db):
    from guardian.core.database import insert_action
    import guardian.actions.executor as exc

    token = "deny-api-tok"
    event = asyncio.Event()
    exc._pending[token] = event
    await insert_action(None, "ban_ip", {"target": "5.5.5.5"}, "critical", "pending", approval_token=token)

    resp = await api.post(f"/actions/deny/{token}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"
    assert event.is_set()


async def test_deny_post_not_found(api, db):
    resp = await api.post("/actions/deny/ghost")
    assert resp.status_code == 404


async def test_deny_get_returns_plain_text(api, db):
    from guardian.core.database import insert_action
    import guardian.actions.executor as exc

    token = "get-deny-tok"
    event = asyncio.Event()
    exc._pending[token] = event
    await insert_action(None, "alert_only", {}, "critical", "pending", approval_token=token)

    resp = await api.get(f"/actions/deny/{token}")
    assert resp.status_code == 200
    assert "DENIED" in resp.text


# ── /emergency-stop ───────────────────────────────────────────────────────────

async def test_emergency_stop_activate(api, monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)

    resp = await api.post("/emergency-stop", json={"stop": True, "reason": "test"})
    assert resp.status_code == 200
    assert resp.json()["emergency_stop"] is True
    assert cfg.safety.emergency_stop is True


async def test_emergency_stop_deactivate(api, monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", True)

    resp = await api.post("/emergency-stop", json={"stop": False, "reason": "clear"})
    assert resp.status_code == 200
    assert resp.json()["emergency_stop"] is False


# ── /config ───────────────────────────────────────────────────────────────────

async def test_config_endpoint(api):
    resp = await api.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "service_name" in data
    assert "dry_run" in data
    assert "ai_enabled" in data


# ── /decisions ────────────────────────────────────────────────────────────────

async def test_decisions_empty(api):
    resp = await api.get("/decisions")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_decisions_returns_inserted(api, db):
    from guardian.core.database import insert_decision

    await insert_decision(
        context={},
        reasoning="all good",
        summary="System healthy",
        confidence=0.95,
        actions=[],
        model="llama3.2:3b",
    )

    resp = await api.get("/decisions")
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["summary"] == "System healthy"
