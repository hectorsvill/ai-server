"""
Notification service — Telegram, Discord, and Slack.
Used for:
  - Critical events / alerts
  - Approval requests for high-risk actions (with approve/deny buttons/links)
  - Recovery confirmations
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from guardian.core.config import cfg
from guardian.core.logger import get_logger

log = get_logger(__name__)


class NotificationService:

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ── Routing ────────────────────────────────────────────────────────────────

    async def notify(self, message: str, level: str = "info") -> None:
        """Send a plain notification to all enabled channels."""
        if not cfg.notifications.enabled:
            log.debug("notifications_disabled_skipping", message=message[:80])
            return

        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨", "ok": "✅"}.get(level, "📋")
        text = f"{emoji} *[AI Guardian — {cfg.server.domain}]*\n{message}"

        tasks = []
        if cfg.notifications.telegram.enabled:
            tasks.append(self._send_telegram(text))
        if cfg.notifications.discord.enabled:
            tasks.append(self._send_discord(text, level))
        if cfg.notifications.slack.enabled:
            tasks.append(self._send_slack(text))

        for coro in tasks:
            try:
                await coro
            except Exception as e:
                log.error("notification_send_failed", error=str(e))

    async def send_approval_request(
        self,
        action_id: int,
        token: str,
        action_type: str,
        target: str,
        risk_level: str,
        reason: str,
    ) -> None:
        """Send an approval request. Telegram gets inline keyboard buttons; other channels get the token for manual /approve /deny."""
        if not cfg.notifications.enabled:
            return

        risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(risk_level, "⚪")

        base_text = (
            f"🤖 *AI Guardian — Action Approval Required*\n\n"
            f"{risk_emoji} *Risk Level:* {risk_level.upper()}\n"
            f"📋 *Action:* `{action_type}`\n"
            f"🎯 *Target:* `{target}`\n"
            f"💡 *Reason:* {reason}\n\n"
            f"_Action ID: {action_id} | Expires in 10 min_"
        )

        tasks = []
        if cfg.notifications.telegram.enabled:
            tasks.append(self._send_telegram_approval(base_text, token))
        if cfg.notifications.discord.enabled:
            fallback = base_text + f"\n\nReply `/approve {token}` or `/deny {token}` via Telegram bot."
            tasks.append(self._send_discord(fallback, "warning"))
        if cfg.notifications.slack.enabled:
            fallback = base_text + f"\n\nReply `/approve {token}` or `/deny {token}` via Telegram bot."
            tasks.append(self._send_slack(fallback))

        for coro in tasks:
            try:
                await coro
            except Exception as e:
                log.error("approval_request_send_failed", error=str(e))

        log.info(
            "approval_request_sent",
            action_id=action_id,
            token=token,
            action_type=action_type,
            target=target,
        )

    async def _send_telegram_approval(self, text: str, token: str) -> None:
        t_cfg = cfg.notifications.telegram
        bot_token = t_cfg.bot_token or os.environ.get("GUARDIAN_TELEGRAM_TOKEN", "")
        chat_id = t_cfg.chat_id or os.environ.get("GUARDIAN_TELEGRAM_CHAT_ID", "")

        if not bot_token or not chat_id:
            log.warning("telegram_not_configured")
            return

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ APPROVE", "callback_data": f"approve:{token}"},
                    {"text": "❌ DENY",    "callback_data": f"deny:{token}"},
                ]]
            },
        }
        resp = await self._client.post(url, json=payload)
        if resp.status_code != 200:
            log.error("telegram_approval_send_failed", status=resp.status_code, body=resp.text[:200])

    # ── Channel implementations ────────────────────────────────────────────────

    async def _send_telegram(self, text: str) -> None:
        t_cfg = cfg.notifications.telegram
        token = t_cfg.bot_token or os.environ.get("GUARDIAN_TELEGRAM_TOKEN", "")
        chat_id = t_cfg.chat_id or os.environ.get("GUARDIAN_TELEGRAM_CHAT_ID", "")

        if not token or not chat_id:
            log.warning("telegram_not_configured")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = await self._client.post(url, json=payload)
        if resp.status_code != 200:
            log.error("telegram_send_failed", status=resp.status_code, body=resp.text[:200])

    async def _send_discord(self, text: str, level: str = "info") -> None:
        d_cfg = cfg.notifications.discord
        webhook_url = d_cfg.webhook_url or os.environ.get("GUARDIAN_DISCORD_WEBHOOK", "")

        if not webhook_url:
            log.warning("discord_not_configured")
            return

        color_map = {"info": 3447003, "warning": 16776960, "critical": 15158332, "ok": 3066993}
        color = color_map.get(level, 3447003)

        payload = {
            "embeds": [{
                "title": f"AI Guardian — {cfg.server.domain}",
                "description": text.replace("*", "**"),
                "color": color,
            }]
        }
        resp = await self._client.post(webhook_url, json=payload)
        if resp.status_code not in (200, 204):
            log.error("discord_send_failed", status=resp.status_code)

    async def _send_slack(self, text: str) -> None:
        s_cfg = cfg.notifications.slack
        webhook_url = s_cfg.webhook_url or os.environ.get("GUARDIAN_SLACK_WEBHOOK", "")

        if not webhook_url:
            log.warning("slack_not_configured")
            return

        payload = {"text": text}
        resp = await self._client.post(webhook_url, json=payload)
        if resp.status_code != 200:
            log.error("slack_send_failed", status=resp.status_code)
