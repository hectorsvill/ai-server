"""Tests for guardian.core.database — all CRUD operations."""
from __future__ import annotations

import time

import pytest


async def test_insert_and_get_metric(db):
    from guardian.core.database import get_latest_metrics, insert_metric

    row_id = await insert_metric("system", {"cpu": 42, "ram": 55})
    assert row_id > 0

    rows = await get_latest_metrics("system", limit=1)
    assert len(rows) == 1
    assert rows[0]["data"]["cpu"] == 42
    assert rows[0]["data"]["ram"] == 55


async def test_get_metrics_filtered_by_type(db):
    from guardian.core.database import get_latest_metrics, insert_metric

    await insert_metric("system", {"cpu": 10})
    await insert_metric("docker", {"containers": 3})

    sys_rows = await get_latest_metrics("system")
    docker_rows = await get_latest_metrics("docker")

    assert all(r["data"].get("cpu") is not None for r in sys_rows)
    assert all(r["data"].get("containers") is not None for r in docker_rows)


async def test_insert_and_get_event(db):
    from guardian.core.database import get_recent_events, insert_event

    eid = await insert_event("critical", "security", "SSH Brute Force", "100 attempts from 1.2.3.4")
    assert eid > 0

    rows = await get_recent_events(limit=10)
    assert any(r["title"] == "SSH Brute Force" for r in rows)


async def test_get_events_unresolved_filter(db):
    from guardian.core.database import get_recent_events, insert_event, resolve_event

    eid = await insert_event("warning", "system", "High CPU", "CPU at 95%")
    await insert_event("info", "docker", "Container restart", "nginx restarted")

    await resolve_event(eid)

    unresolved = await get_recent_events(unresolved_only=True)
    titles = [r["title"] for r in unresolved]
    assert "High CPU" not in titles
    assert "Container restart" in titles


async def test_get_events_severity_filter(db):
    from guardian.core.database import get_recent_events, insert_event

    await insert_event("critical", "security", "Critical Event", "desc")
    await insert_event("warning", "system", "Warning Event", "desc")
    await insert_event("info", "docker", "Info Event", "desc")

    criticals = await get_recent_events(severity="critical")
    assert all(r["severity"] == "critical" for r in criticals)
    assert any(r["title"] == "Critical Event" for r in criticals)


async def test_insert_and_get_decision(db):
    from guardian.core.database import get_recent_decisions, insert_decision

    did = await insert_decision(
        context={"cpu": 80},
        reasoning="CPU is very high",
        summary="Restart overloaded container",
        confidence=0.91,
        actions=[{"action_type": "restart_container", "target": "webui"}],
        model="llama3.2:3b",
    )
    assert did > 0

    rows = await get_recent_decisions(limit=5)
    assert any(r["summary"] == "Restart overloaded container" for r in rows)
    assert rows[0]["confidence"] == pytest.approx(0.91, abs=0.001)


async def test_insert_action_and_get_by_token(db):
    from guardian.core.database import get_action_by_token, insert_action

    token = "tok-abc-123"
    aid = await insert_action(
        decision_id=None,
        action_type="ban_ip",
        parameters={"target": "1.2.3.4"},
        risk_level="critical",
        status="pending",
        approval_token=token,
    )
    assert aid > 0

    row = await get_action_by_token(token)
    assert row is not None
    assert row["action_type"] == "ban_ip"
    assert row["status"] == "pending"
    assert row["approval_token"] == token


async def test_get_action_by_token_missing_returns_none(db):
    from guardian.core.database import get_action_by_token

    row = await get_action_by_token("does-not-exist")
    assert row is None


async def test_get_pending_approvals(db):
    from guardian.core.database import get_pending_approvals, insert_action, update_action

    tok1 = await insert_action(None, "restart_container", {"target": "n8n"}, "high", "pending", approval_token="t1")
    tok2 = await insert_action(None, "ban_ip", {"target": "9.9.9.9"}, "critical", "pending", approval_token="t2")
    tok3 = await insert_action(None, "alert_only", {}, "low", "completed", approval_token="t3")

    pending = await get_pending_approvals()
    statuses = [r["status"] for r in pending]
    assert all(s == "pending" for s in statuses)
    assert len(pending) == 2


async def test_update_action_status(db):
    from guardian.core.database import get_action_by_token, insert_action, update_action

    token = "upd-tok"
    await insert_action(None, "alert_only", {}, "low", "pending", approval_token=token)
    await update_action(1, "completed", result={"alerted": True}, approved_by="test")

    row = await get_action_by_token(token)
    assert row["status"] == "completed"


async def test_get_recent_actions_ordering(db):
    from guardian.core.database import get_recent_actions, insert_action

    await insert_action(None, "alert_only", {}, "low", "completed", approval_token="r1")
    await insert_action(None, "restart_container", {"target": "caddy"}, "medium", "completed", approval_token="r2")

    rows = await get_recent_actions(limit=10)
    assert len(rows) >= 2
    # Most recent first
    assert rows[0]["approval_token"] == "r2"


async def test_purge_old_metrics(db):
    from guardian.core.database import get_latest_metrics, insert_metric, purge_old_metrics
    import aiosqlite

    import guardian.core.database as db_module

    # Insert a metric with a very old timestamp
    async with aiosqlite.connect(db_module.DB_PATH) as conn:
        old_ts = time.time() - 40 * 86400  # 40 days ago
        await conn.execute(
            "INSERT INTO metrics(timestamp, metric_type, data) VALUES(?,?,?)",
            (old_ts, "system", '{"cpu": 1}'),
        )
        await conn.commit()

    # Insert a fresh metric
    await insert_metric("system", {"cpu": 99})

    deleted = await purge_old_metrics(retention_days=30)
    assert deleted >= 1

    rows = await get_latest_metrics("system", limit=10)
    assert all(r["data"]["cpu"] != 1 for r in rows)


async def test_dry_run_action_recorded(db):
    from guardian.core.database import get_recent_actions, insert_action

    await insert_action(None, "alert_only", {}, "low", "dry_run", dry_run=True, approval_token="dry-1")

    rows = await get_recent_actions(limit=5)
    dry = [r for r in rows if r["approval_token"] == "dry-1"]
    assert len(dry) == 1
    assert dry[0]["dry_run"] == 1
