"""
Telegram bot — two-way command interface for AI Guardian.

Polls the Telegram Bot API for incoming messages and responds with
live data from the Guardian database and monitors.

Supported commands:
  /status    — system + docker snapshot
  /events    — last 10 unresolved events
  /actions   — last 10 actions taken
  /pending   — actions awaiting approval
  /approve <token>  — approve a pending action
  /deny <token>     — deny a pending action
  /stop      — activate emergency stop
  /resume    — deactivate emergency stop
  /help      — list commands
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from guardian.core.config import cfg
from guardian.core.database import (
    get_pending_approvals,
    get_recent_actions,
    get_recent_events,
    get_latest_metrics,
)
from guardian.core.logger import get_logger
from guardian.actions.executor import approve_action, deny_action

log = get_logger(__name__)

_POLL_INTERVAL = 2       # seconds between getUpdates calls
_POLL_TIMEOUT  = 30      # long-poll timeout (seconds)


class TelegramBot:
    def __init__(self) -> None:
        self._token   = cfg.notifications.telegram.bot_token or os.environ.get("GUARDIAN_TELEGRAM_TOKEN", "")
        self._chat_id = cfg.notifications.telegram.chat_id   or os.environ.get("GUARDIAN_TELEGRAM_CHAT_ID", "")
        self._base    = f"https://api.telegram.org/bot{self._token}"
        self._offset  = 0
        self._client  = httpx.AsyncClient(timeout=40.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _enabled(self) -> bool:
        return bool(self._token and self._chat_id and cfg.notifications.telegram.enabled)

    # ── Low-level API ─────────────────────────────────────────────────────────

    async def _api(self, method: str, **params) -> dict | None:
        try:
            resp = await self._client.post(f"{self._base}/{method}", json=params)
            data = resp.json()
            if not data.get("ok"):
                log.warning("telegram_api_error", method=method, description=data.get("description"))
                return None
            return data.get("result")
        except Exception as e:
            log.error("telegram_api_exception", method=method, error=str(e))
            return None

    async def send(self, text: str, parse_mode: str = "Markdown") -> None:
        await self._api(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )

    async def _get_updates(self) -> list[dict]:
        result = await self._api(
            "getUpdates",
            offset=self._offset,
            timeout=_POLL_TIMEOUT,
            allowed_updates=["message"],
        )
        return result or []

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _cmd_status(self) -> str:
        sys_rows    = await get_latest_metrics("system", limit=1)
        docker_rows = await get_latest_metrics("docker", limit=1)

        lines = [f"📊 *AI Guardian — {cfg.server.domain}*\n"]

        if sys_rows:
            d   = sys_rows[0]["data"]
            cpu = d.get("cpu", {})
            mem = d.get("memory", {})
            disks = d.get("disks", {})
            lines.append("*System*")
            lines.append(f"  CPU: `{cpu.get('percent', '?')}%`  Load: `{cpu.get('load_avg', {}).get('1m', '?')}`")
            lines.append(f"  RAM: `{mem.get('pct', '?')}%` ({mem.get('used_gb','?')} / {mem.get('total_gb','?')} GB)")
            for mount, info in disks.items():
                lines.append(f"  Disk `{mount}`: `{info['pct']}%` ({info['used_gb']}/{info['total_gb']} GB)")
        else:
            lines.append("_No system metrics yet_")

        # GPU stats
        gpu = d.get("gpu", {})
        if gpu.get("available"):
            def _bar(pct, warn, crit):
                if pct is None:
                    return "?"
                icon = "🔴" if pct >= crit else ("🟡" if pct >= warn else "🟢")
                return f"{icon} `{pct}%`"

            t = gpu.get("temp_junc_c")
            t_icon = "🔴" if t and t >= 100 else ("🟡" if t and t >= 85 else "🌡")
            lines.append("\n*GPU — RX 7900 GRE*")
            lines.append(f"  Use:  {_bar(gpu.get('gpu_pct'), 90, 99)}  "
                         f"VRAM: {_bar(gpu.get('vram_pct'), 80, 95)}")
            lines.append(f"  Temp: {t_icon} `{t}°C`  "
                         f"Power: `{gpu.get('power_w')}W / {gpu.get('power_max_w')}W`  "
                         f"Clk: `{gpu.get('sclk')}`")
        elif gpu.get("error"):
            lines.append(f"\n_GPU: {gpu['error']}_")

        lines.append("")

        if docker_rows:
            d = docker_rows[0]["data"]
            containers = d.get("containers", {})
            lines.append("*Containers*")
            for name, info in containers.items():
                icon = "🟢" if info["status"] == "running" else "🔴"
                health = f" [{info['health']}]" if info["health"] not in ("none", "healthy") else ""
                restarts = f" ↺{info['restart_count']}" if info["restart_count"] > 0 else ""
                lines.append(f"  {icon} `{name}`{health}{restarts}")
        else:
            lines.append("_No docker metrics yet_")

        dry   = "✅ DRY-RUN" if cfg.safety.dry_run else ""
        estop = "🛑 EMERGENCY STOP" if cfg.safety.emergency_stop else ""
        flags = "  ".join(f for f in [dry, estop] if f)
        if flags:
            lines.append(f"\n{flags}")

        return "\n".join(lines)

    async def _cmd_events(self) -> str:
        events = await get_recent_events(limit=10, unresolved_only=True)
        if not events:
            return "✅ No unresolved events."

        icons = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
        lines = [f"*Unresolved Events ({len(events)})*\n"]
        for ev in events:
            icon = icons.get(ev["severity"], "📋")
            lines.append(f"{icon} *{ev['title']}*")
            lines.append(f"   _{ev['description'][:120]}_\n")
        return "\n".join(lines)

    async def _cmd_actions(self) -> str:
        actions = await get_recent_actions(limit=10)
        if not actions:
            return "_No actions recorded yet._"

        import json
        status_icons = {
            "completed": "✅", "failed": "❌", "dry_run": "🔵",
            "denied": "🚫", "pending": "⏳", "approved": "👍",
        }
        lines = ["*Last 10 Actions*\n"]
        for a in actions:
            icon  = status_icons.get(a["status"], "📋")
            params = {}
            try:
                params = json.loads(a.get("parameters") or "{}")
            except Exception:
                pass
            target = params.get("target", "")
            target_str = f" → `{target}`" if target else ""
            lines.append(f"{icon} `{a['action_type']}`{target_str} [{a['risk_level']}] — {a['status']}")
        return "\n".join(lines)

    async def _cmd_pending(self) -> str:
        import json
        pending = await get_pending_approvals()
        if not pending:
            return "✅ No actions pending approval."

        lines = [f"*Pending Approvals ({len(pending)})*\n"]
        for a in pending:
            params = {}
            try:
                params = json.loads(a.get("parameters") or "{}")
            except Exception:
                pass
            target = params.get("target", "")
            lines.append(
                f"🟠 *{a['action_type']}* → `{target}` [{a['risk_level']}]\n"
                f"   Token: `{a['approval_token']}`\n"
                f"   Reply: `/approve {a['approval_token']}` or `/deny {a['approval_token']}`\n"
            )
        return "\n".join(lines)

    async def _cmd_approve(self, token: str) -> str:
        ok = await approve_action(token, approved_by="telegram_command")
        return "✅ Action approved." if ok else "❌ Token not found or already resolved."

    async def _cmd_deny(self, token: str) -> str:
        ok = await deny_action(token, denied_by="telegram_command")
        return "🚫 Action denied." if ok else "❌ Token not found or already resolved."

    async def _cmd_stop(self) -> str:
        cfg.safety.emergency_stop = True
        log.warning("emergency_stop_via_telegram")
        return "🛑 *Emergency stop ACTIVATED.* All automated actions halted.\nSend /resume to re-enable."

    async def _cmd_resume(self) -> str:
        cfg.safety.emergency_stop = False
        log.info("emergency_stop_cleared_via_telegram")
        return "✅ *Emergency stop cleared.* Automated actions resumed."

    _HELP = (
        "🤖 *AI Guardian Commands*\n\n"
        "/status — system & container snapshot\n"
        "/events — unresolved alerts\n"
        "/actions — recent action history\n"
        "/pending — actions awaiting approval\n"
        "/approve `<token>` — approve a pending action\n"
        "/deny `<token>` — deny a pending action\n"
        "/stop — activate emergency stop\n"
        "/resume — deactivate emergency stop\n"
        "/help — this message"
    )

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        text    = (message.get("text") or "").strip()

        # Only respond to the configured chat
        if chat_id != str(self._chat_id):
            log.warning("telegram_unauthorized_chat", chat_id=chat_id)
            return

        if not text.startswith("/"):
            return

        parts   = text.split()
        command = parts[0].lower().split("@")[0]   # strip @botname suffix
        args    = parts[1:]

        log.info("telegram_command", command=command, args=args)

        try:
            if command == "/status":
                reply = await self._cmd_status()
            elif command == "/events":
                reply = await self._cmd_events()
            elif command == "/actions":
                reply = await self._cmd_actions()
            elif command == "/pending":
                reply = await self._cmd_pending()
            elif command == "/approve":
                reply = await self._cmd_approve(args[0]) if args else "Usage: /approve <token>"
            elif command == "/deny":
                reply = await self._cmd_deny(args[0]) if args else "Usage: /deny <token>"
            elif command == "/stop":
                reply = await self._cmd_stop()
            elif command == "/resume":
                reply = await self._cmd_resume()
            elif command == "/help" or command == "/start":
                reply = self._HELP
            else:
                reply = f"Unknown command `{command}`. Send /help for the list."
        except Exception as e:
            log.error("telegram_command_error", command=command, exc_info=e)
            reply = f"❌ Error: {e}"

        await self.send(reply)

    # ── Main polling loop ──────────────────────────────────────────────────────

    async def poll_loop(self, stop_event: asyncio.Event) -> None:
        if not self._enabled():
            log.info("telegram_bot_disabled_skipping_poll")
            return

        log.info("telegram_bot_poll_started", chat_id=self._chat_id)
        # Clear any queued old updates on startup
        await self._api("getUpdates", offset=-1, timeout=1)

        while not stop_event.is_set():
            try:
                updates = await self._get_updates()
                for update in updates:
                    self._offset = update["update_id"] + 1
                    if msg := update.get("message"):
                        await self._handle_message(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("telegram_poll_error", exc_info=e)
                await asyncio.sleep(5)

            # Short sleep between polls (long-poll handles the wait server-side)
            await asyncio.sleep(0.5)

        log.info("telegram_bot_poll_stopped")
