"""Tests for guardian.notifications.webhook — Telegram inline keyboard, notification routing."""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, call

import pytest


def _ok_response():
    r = MagicMock()
    r.status_code = 200
    return r


def _make_service(monkeypatch, *, telegram=True, discord=False, slack=False):
    from guardian.core.config import cfg
    from guardian.notifications.webhook import NotificationService

    t_cfg = types.SimpleNamespace(enabled=telegram, bot_token="tok123", chat_id="99999")
    d_cfg = types.SimpleNamespace(enabled=discord, webhook_url="https://discord.example/hook")
    s_cfg = types.SimpleNamespace(enabled=slack, webhook_url="https://slack.example/hook")

    monkeypatch.setattr(cfg.notifications, "enabled", True)
    monkeypatch.setattr(cfg.notifications, "telegram", t_cfg)
    monkeypatch.setattr(cfg.notifications, "discord", d_cfg)
    monkeypatch.setattr(cfg.notifications, "slack", s_cfg)

    svc = NotificationService()
    svc._client = AsyncMock()
    svc._client.post = AsyncMock(return_value=_ok_response())
    return svc


# ── send_approval_request — Telegram inline keyboard ─────────────────────────

async def test_approval_request_sends_inline_keyboard(monkeypatch):
    svc = _make_service(monkeypatch, telegram=True)

    await svc.send_approval_request(
        action_id=7,
        token="tok-abc",
        action_type="ban_ip",
        target="203.0.113.5",
        risk_level="critical",
        reason="SSH brute force",
    )

    svc._client.post.assert_called_once()
    payload = svc._client.post.call_args.kwargs["json"]

    assert "reply_markup" in payload
    kb = payload["reply_markup"]["inline_keyboard"]
    assert len(kb) == 1  # one row
    assert len(kb[0]) == 2  # two buttons

    approve_btn, deny_btn = kb[0]
    assert approve_btn["text"] == "✅ APPROVE"
    assert approve_btn["callback_data"] == "approve:tok-abc"
    assert deny_btn["text"] == "❌ DENY"
    assert deny_btn["callback_data"] == "deny:tok-abc"


async def test_approval_request_message_contains_action_details(monkeypatch):
    svc = _make_service(monkeypatch, telegram=True)

    await svc.send_approval_request(
        action_id=3,
        token="xyz",
        action_type="restart_container",
        target="open-webui",
        risk_level="high",
        reason="Container unhealthy for 5 minutes",
    )

    payload = svc._client.post.call_args.kwargs["json"]
    text = payload["text"]

    assert "restart_container" in text
    assert "open-webui" in text
    assert "HIGH" in text
    assert "Container unhealthy" in text


async def test_approval_request_no_urls_in_message(monkeypatch):
    """Inline keyboard replaces URL links — message should contain no localhost URLs."""
    svc = _make_service(monkeypatch, telegram=True)

    await svc.send_approval_request(
        action_id=1,
        token="t",
        action_type="ban_ip",
        target="1.2.3.4",
        risk_level="critical",
        reason="test",
    )

    payload = svc._client.post.call_args.kwargs["json"]
    assert "127.0.0.1" not in payload["text"]
    assert "http://" not in payload["text"]


# ── notify — plain notifications ──────────────────────────────────────────────

async def test_notify_sends_to_telegram(monkeypatch):
    svc = _make_service(monkeypatch, telegram=True)

    await svc.notify("Test notification", level="warning")

    svc._client.post.assert_called_once()
    payload = svc._client.post.call_args.kwargs["json"]
    assert "Test notification" in payload["text"]
    assert "⚠️" in payload["text"]


async def test_notify_skips_when_notifications_disabled(monkeypatch):
    from guardian.core.config import cfg
    from guardian.notifications.webhook import NotificationService

    monkeypatch.setattr(cfg.notifications, "enabled", False)

    svc = NotificationService()
    svc._client = AsyncMock()
    svc._client.post = AsyncMock(return_value=_ok_response())

    await svc.notify("should not send")

    svc._client.post.assert_not_called()


async def test_notify_sends_to_discord(monkeypatch):
    svc = _make_service(monkeypatch, telegram=False, discord=True)

    await svc.notify("Discord alert", level="critical")

    svc._client.post.assert_called_once()
    payload = svc._client.post.call_args.kwargs["json"]
    assert "embeds" in payload


async def test_approval_request_skipped_when_notifications_disabled(monkeypatch):
    from guardian.core.config import cfg
    from guardian.notifications.webhook import NotificationService

    monkeypatch.setattr(cfg.notifications, "enabled", False)

    svc = NotificationService()
    svc._client = AsyncMock()
    svc._client.post = AsyncMock(return_value=_ok_response())

    await svc.send_approval_request(1, "tok", "ban_ip", "1.1.1.1", "critical", "test")

    svc._client.post.assert_not_called()


# ── risk emoji mapping ────────────────────────────────────────────────────────

async def test_risk_level_emoji_in_message(monkeypatch):
    svc = _make_service(monkeypatch, telegram=True)

    await svc.send_approval_request(1, "t", "alert_only", "none", "critical", "test")
    payload = svc._client.post.call_args.kwargs["json"]
    assert "🔴" in payload["text"]

    svc._client.post.reset_mock()
    await svc.send_approval_request(2, "t2", "alert_only", "none", "low", "test")
    payload = svc._client.post.call_args.kwargs["json"]
    assert "🟢" in payload["text"]
