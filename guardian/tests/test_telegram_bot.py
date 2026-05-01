"""Tests for guardian.notifications.telegram_bot — commands and inline keyboard callbacks."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_bot(chat_id: str = "12345"):
    from guardian.notifications.telegram_bot import TelegramBot

    bot = TelegramBot()
    bot._token = "fake_token"
    bot._chat_id = chat_id
    bot._api = AsyncMock(return_value=None)
    return bot


def _message(text: str, chat_id: str = "12345") -> dict:
    return {
        "message_id": 1,
        "chat": {"id": int(chat_id)},
        "text": text,
    }


def _callback_query(data: str, chat_id: str = "12345", message_id: int = 10) -> dict:
    return {
        "id": "cq-id-001",
        "data": data,
        "message": {
            "message_id": message_id,
            "chat": {"id": int(chat_id)},
        },
    }


# ── Unauthorized chat ─────────────────────────────────────────────────────────

async def test_unauthorized_chat_is_ignored():
    bot = _make_bot(chat_id="12345")

    await bot._handle_message(_message("/status", chat_id="99999"))

    bot._api.assert_not_called()


async def test_unauthorized_callback_query_rejected():
    bot = _make_bot(chat_id="12345")

    await bot._handle_callback_query(_callback_query("approve:tok", chat_id="99999"))

    bot._api.assert_called_once_with(
        "answerCallbackQuery", callback_query_id="cq-id-001", text="Unauthorized"
    )


# ── /status command ───────────────────────────────────────────────────────────

async def test_status_command_sends_reply(db):
    from guardian.core.database import insert_metric

    await insert_metric("system", {
        "cpu": {"percent": 30, "load_avg": {"1m": 0.8}},
        "memory": {"pct": 50, "used_gb": 8, "total_gb": 16},
        "disks": {"/": {"pct": 45, "used_gb": 50, "total_gb": 110}},
        "gpu": {"available": False},
    })
    await insert_metric("docker", {"containers": {}})

    bot = _make_bot()
    await bot._handle_message(_message("/status"))

    bot._api.assert_called()
    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "Guardian" in sent_text or "System" in sent_text


# ── /events command ───────────────────────────────────────────────────────────

async def test_events_command_no_events(db):
    bot = _make_bot()
    await bot._handle_message(_message("/events"))

    bot._api.assert_called()
    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "No unresolved" in sent_text


async def test_events_command_shows_events(db):
    from guardian.core.database import insert_event

    await insert_event("critical", "security", "SSH Brute Force", "50 attempts")

    bot = _make_bot()
    await bot._handle_message(_message("/events"))

    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "SSH Brute Force" in sent_text


# ── /actions command ──────────────────────────────────────────────────────────

async def test_actions_command_no_actions(db):
    bot = _make_bot()
    await bot._handle_message(_message("/actions"))

    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "No actions" in sent_text


async def test_actions_command_shows_action_history(db):
    from guardian.core.database import insert_action

    await insert_action(None, "ban_ip", {"target": "1.2.3.4"}, "critical", "completed", approval_token="t1")

    bot = _make_bot()
    await bot._handle_message(_message("/actions"))

    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "ban_ip" in sent_text


# ── /pending command ──────────────────────────────────────────────────────────

async def test_pending_command_no_pending(db):
    bot = _make_bot()
    await bot._handle_message(_message("/pending"))

    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "No actions pending" in sent_text


async def test_pending_command_shows_token(db):
    from guardian.core.database import insert_action

    await insert_action(None, "ban_ip", {"target": "5.5.5.5"}, "critical", "pending", approval_token="show-me")

    bot = _make_bot()
    await bot._handle_message(_message("/pending"))

    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "show-me" in sent_text


# ── /approve and /deny text commands ─────────────────────────────────────────

async def test_approve_command_with_valid_token():
    bot = _make_bot()

    token = "cmd-approve-tok"
    event = asyncio.Event()

    import guardian.actions.executor as exc
    exc._pending[token] = event

    await bot._handle_message(_message(f"/approve {token}"))

    assert event.is_set()
    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "approved" in sent_text.lower()


async def test_approve_command_missing_token():
    bot = _make_bot()
    await bot._handle_message(_message("/approve"))

    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "Usage" in sent_text


async def test_deny_command_with_valid_token():
    bot = _make_bot()

    token = "cmd-deny-tok"
    event = asyncio.Event()

    import guardian.actions.executor as exc
    exc._pending[token] = event

    await bot._handle_message(_message(f"/deny {token}"))

    assert event.is_set()


# ── /stop and /resume ─────────────────────────────────────────────────────────

async def test_stop_command_activates_emergency_stop(monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", False)
    bot = _make_bot()
    await bot._handle_message(_message("/stop"))

    assert cfg.safety.emergency_stop is True
    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "ACTIVATED" in sent_text


async def test_resume_command_clears_emergency_stop(monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg.safety, "emergency_stop", True)
    bot = _make_bot()
    await bot._handle_message(_message("/resume"))

    assert cfg.safety.emergency_stop is False
    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    assert "cleared" in sent_text.lower()


# ── /help command ─────────────────────────────────────────────────────────────

async def test_help_command_lists_commands():
    bot = _make_bot()
    await bot._handle_message(_message("/help"))

    sent_text = bot._api.call_args_list[-1].kwargs.get("text", "")
    for cmd in ["/status", "/events", "/actions", "/pending", "/approve", "/deny", "/stop"]:
        assert cmd in sent_text


# ── Inline keyboard callback queries ─────────────────────────────────────────

async def test_callback_approve_resolves_action():
    bot = _make_bot()

    token = "cq-approve-tok"
    event = asyncio.Event()
    import guardian.actions.executor as exc
    exc._pending[token] = event

    await bot._handle_callback_query(_callback_query(f"approve:{token}"))

    assert event.is_set()
    # answerCallbackQuery called with ✅
    answer_call = bot._api.call_args_list[0]
    assert answer_call.kwargs.get("text") == "✅ Approved"


async def test_callback_deny_resolves_action():
    bot = _make_bot()

    token = "cq-deny-tok"
    event = asyncio.Event()
    import guardian.actions.executor as exc
    exc._pending[token] = event

    await bot._handle_callback_query(_callback_query(f"deny:{token}"))

    assert event.is_set()
    answer_call = bot._api.call_args_list[0]
    assert answer_call.kwargs.get("text") == "❌ Denied"


async def test_callback_removes_keyboard_after_action():
    """After approve/deny, the inline keyboard is removed from the original message."""
    bot = _make_bot()

    token = "remove-kb-tok"
    event = asyncio.Event()
    import guardian.actions.executor as exc
    exc._pending[token] = event

    await bot._handle_callback_query(_callback_query(f"approve:{token}", message_id=42))

    # Find the editMessageReplyMarkup call
    methods_called = [c.args[0] for c in bot._api.call_args_list]
    assert "editMessageReplyMarkup" in methods_called


async def test_callback_unknown_data_no_crash():
    bot = _make_bot()

    cq = _callback_query("unknown:whatever")
    await bot._handle_callback_query(cq)

    # answerCallbackQuery should still be called
    assert bot._api.called


async def test_non_command_message_triggers_ollama(monkeypatch):
    """Plain text messages (not /commands) are routed to Ollama chat."""
    bot = _make_bot()

    ollama_reply = AsyncMock(return_value="Looks healthy!")
    monkeypatch.setattr(bot, "_reply_ollama", ollama_reply)

    await bot._handle_message(_message("Is everything ok?"))

    ollama_reply.assert_called_once_with("Is everything ok?")
