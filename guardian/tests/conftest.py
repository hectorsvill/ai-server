"""Shared fixtures for AI Guardian tests."""
from __future__ import annotations

import asyncio
import types

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """Isolated in-file SQLite database per test."""
    import guardian.core.database as db_module

    test_db = tmp_path / "test_guardian.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db)
    await db_module.init_db()
    return test_db


@pytest.fixture(autouse=True)
def _reset_executor():
    """Clear executor rate-limit and pending-approval state between tests."""
    import guardian.actions.executor as m

    m._action_timestamps.clear()
    m._container_restart_timestamps.clear()
    m._pending.clear()
    m._pending_results.clear()
    yield
    m._action_timestamps.clear()
    m._container_restart_timestamps.clear()
    m._pending.clear()
    m._pending_results.clear()


@pytest.fixture(autouse=True)
def _reset_security():
    """Clear security monitor module-level state between tests."""
    import guardian.monitors.security as m

    m._ssh_failures.clear()
    m._banned_ips.clear()
    m._last_alert.clear()
    m._AUTH_LOG_POSITION = 0
    m._UFW_LOG_POSITION = 0
    yield
    m._ssh_failures.clear()
    m._banned_ips.clear()
    m._last_alert.clear()


def fake_telegram_cfg(bot_token: str = "tok", chat_id: str = "999"):
    """Return a SimpleNamespace that mimics TelegramNotificationCfg."""
    return types.SimpleNamespace(enabled=True, bot_token=bot_token, chat_id=chat_id)
