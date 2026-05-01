"""Tests for guardian.actions.executor — safety gates, routing, approval flow."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_executor(notify_mock=None):
    from guardian.actions.executor import ActionExecutor

    exe = ActionExecutor()
    if notify_mock is None:
        notify_mock = AsyncMock()
        notify_mock.send_approval_request = AsyncMock()
    exe._notify = notify_mock
    return exe


def _low_risk_proposal(**overrides):
    base = {
        "action_type": "alert_only",
        "target": "",
        "risk_level": "low",
        "confidence": 0.95,
        "reason": "test",
        "parameters": {},
    }
    base.update(overrides)
    return base


# ── Safety gate tests ─────────────────────────────────────────────────────────

async def test_emergency_stop_blocks_all_actions(monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", True)
    exe = _make_executor()

    result = await exe.execute(_low_risk_proposal())
    assert result["status"] == "blocked"
    assert result["reason"] == "emergency_stop"


async def test_prohibited_action_blocked(monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", ["delete_volume", "alert_only"])
    exe = _make_executor()

    result = await exe.execute(_low_risk_proposal(action_type="alert_only"))
    assert result["status"] == "prohibited"


async def test_rate_limit_blocks_when_exceeded(monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 0)
    exe = _make_executor()

    result = await exe.execute(_low_risk_proposal())
    assert result["status"] == "rate_limited"


async def test_rate_limit_allows_within_budget(db, monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 10)
    monkeypatch.setattr(cfg.safety, "dry_run", False)
    monkeypatch.setattr(cfg.ai.risk_policy, "low", "auto")
    exe = _make_executor()

    result = await exe.execute(_low_risk_proposal())
    assert result["status"] == "completed"


# ── Dry-run tests ─────────────────────────────────────────────────────────────

async def test_dry_run_does_not_execute(db, monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 100)
    monkeypatch.setattr(cfg.safety, "dry_run", True)
    exe = _make_executor()

    result = await exe.execute(_low_risk_proposal(reason="would do something"))
    assert result["status"] == "dry_run"
    assert "action_id" in result


async def test_dry_run_records_simulated_result(db, monkeypatch):
    from guardian.core.config import cfg
    from guardian.core.database import get_recent_actions

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 100)
    monkeypatch.setattr(cfg.safety, "dry_run", True)
    exe = _make_executor()

    await exe.execute(_low_risk_proposal())

    rows = await get_recent_actions(limit=1)
    assert rows[0]["status"] == "dry_run"
    assert rows[0]["dry_run"] == 1


# ── Risk routing tests ────────────────────────────────────────────────────────

async def test_low_risk_auto_executes(db, monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 100)
    monkeypatch.setattr(cfg.safety, "dry_run", False)
    monkeypatch.setattr(cfg.ai.risk_policy, "low", "auto")
    exe = _make_executor()

    result = await exe.execute(_low_risk_proposal(risk_level="low"))
    assert result["status"] == "completed"


async def test_medium_risk_auto_with_log(db, monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 100)
    monkeypatch.setattr(cfg.safety, "dry_run", False)
    monkeypatch.setattr(cfg.ai.risk_policy, "medium", "auto_with_log")
    exe = _make_executor()

    result = await exe.execute(_low_risk_proposal(risk_level="medium"))
    assert result["status"] == "completed"


# ── Approval flow tests ───────────────────────────────────────────────────────

async def test_approve_action_resolves_pending():
    from guardian.actions.executor import _pending, _pending_results, approve_action

    token = "approve-me"
    event = asyncio.Event()
    _pending[token] = event

    ok = await approve_action(token, approved_by="tester")

    assert ok is True
    assert event.is_set()
    assert _pending_results[token] is True


async def test_deny_action_resolves_pending():
    from guardian.actions.executor import _pending, _pending_results, deny_action

    token = "deny-me"
    event = asyncio.Event()
    _pending[token] = event

    ok = await deny_action(token, denied_by="tester")

    assert ok is True
    assert event.is_set()
    assert _pending_results[token] is False


async def test_approve_unknown_token_returns_false():
    from guardian.actions.executor import approve_action

    ok = await approve_action("ghost-token")
    assert ok is False


async def test_deny_unknown_token_returns_false():
    from guardian.actions.executor import deny_action

    ok = await deny_action("ghost-token")
    assert ok is False


async def test_high_risk_sends_approval_notification_and_times_out(db, monkeypatch):
    """High-risk action sends a notification and denies on timeout."""
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 100)
    monkeypatch.setattr(cfg.safety, "dry_run", False)
    monkeypatch.setattr(cfg.ai.risk_policy, "critical", "require_approval")

    notify = AsyncMock()
    notify.send_approval_request = AsyncMock()
    exe = _make_executor(notify_mock=notify)

    # Patch the wait timeout to 0.05s so the test is fast
    import guardian.actions.executor as exc_module

    original_wait = asyncio.wait_for

    async def fast_wait(coro, timeout):
        return await original_wait(coro, timeout=0.05)

    monkeypatch.setattr(asyncio, "wait_for", fast_wait)

    result = await exe.execute(_low_risk_proposal(risk_level="critical"))

    notify.send_approval_request.assert_called_once()
    assert result["status"] == "denied"


async def test_approved_high_risk_executes(db, monkeypatch):
    """High-risk action approved by concurrent task executes to completion."""
    from guardian.actions.executor import _pending, approve_action
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    monkeypatch.setattr(cfg.safety, "prohibited_actions", [])
    monkeypatch.setattr(cfg.safety, "max_actions_per_hour", 100)
    monkeypatch.setattr(cfg.safety, "dry_run", False)
    monkeypatch.setattr(cfg.ai.risk_policy, "critical", "require_approval")

    notify = AsyncMock()
    notify.send_approval_request = AsyncMock()
    exe = _make_executor(notify_mock=notify)

    async def approve_when_pending():
        for _ in range(40):
            await asyncio.sleep(0.05)
            if _pending:
                token = next(iter(_pending))
                await approve_action(token, approved_by="test")
                return

    asyncio.create_task(approve_when_pending())
    result = await exe.execute(_low_risk_proposal(risk_level="critical"))
    assert result["status"] == "completed"
