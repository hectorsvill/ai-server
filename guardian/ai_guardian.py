#!/usr/bin/env python3
"""
AI Guardian — Autonomous Linux + Docker server manager for vailab.us
Entry point.  Run directly or via systemd (see ai-guardian.service).

Usage:
    python ai_guardian.py                  # production
    python ai_guardian.py --dry-run        # simulate all actions
    python ai_guardian.py --no-ai          # monitoring only, no LLM
    python ai_guardian.py --port 9900      # override API port
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Bootstrap (must happen before other guardian imports) ─────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from guardian.core.logger import setup_logging, get_logger
from guardian.core.config import cfg, load_config
from guardian.core.database import init_db, purge_old_metrics

setup_logging()
log = get_logger("ai_guardian")

# ── Late imports (after logging is set up) ────────────────────────────────────
from guardian.monitors.system import system_monitor_loop
from guardian.monitors.docker_monitor import docker_monitor_loop
from guardian.monitors.security import security_monitor_loop
from guardian.actions.system_actions import SystemActions
from guardian.ai.reasoning import reasoning_loop
from guardian.api.routes import router, set_scan_trigger
from guardian.notifications.webhook import NotificationService
from guardian.notifications.telegram_bot import TelegramBot


VERSION = "1.0.2"
_system_actions = SystemActions()
_notifier = NotificationService()
_telegram_bot = TelegramBot()


# ── FastAPI app ────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Guardian",
        description=f"Autonomous server manager for {cfg.server.domain}",
        version=VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    # Allow localhost-only CORS (for Glance dashboard widget, etc.)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            f"http://127.0.0.1:{cfg.service.port}",
            f"https://{cfg.server.domain}",
            f"https://dash.{cfg.server.domain}",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


# ── SSH event callback ────────────────────────────────────────────────────────

async def _ssh_event_callback(event_type: str, user: str, ip: str) -> None:
    """Called by security monitor on every SSH connect and disconnect."""
    if event_type == "connect":
        message = f"🔐 SSH connected: `{user}` from `{ip}`"
    else:
        message = f"🔓 SSH disconnected: `{user}` from `{ip}`"
    await _notifier.notify(message, level="info")


# ── Ban callback ──────────────────────────────────────────────────────────────

async def _ban_callback(ip: str) -> None:
    """Called by security monitor when brute-force threshold is crossed."""
    if cfg.security.auto_ban_ssh.enabled and not cfg.safety.dry_run:
        try:
            result = await _system_actions.ban_ip(
                ip, cfg.security.auto_ban_ssh.ban_duration_minutes
            )
            log.warning("auto_ban_executed", ip=ip, result=result)
            await _notifier.notify(
                f"🚫 Auto-banned IP `{ip}` for SSH brute-force "
                f"({cfg.security.auto_ban_ssh.ban_duration_minutes} min)\n"
                f"UFW: {result.get('ufw_output', '')}",
                level="critical",
            )
        except Exception as e:
            log.error("auto_ban_failed", ip=ip, error=str(e))
    elif cfg.safety.dry_run:
        log.info("auto_ban_dry_run", ip=ip)


# ── Maintenance task ──────────────────────────────────────────────────────────

async def maintenance_loop(stop_event: asyncio.Event) -> None:
    """
    Runs housekeeping tasks:
    - Purge old metrics from DB (daily)
    - Trigger disk cleanup if threshold reached
    """
    log.info("maintenance_loop_started")
    while not stop_event.is_set():
        try:
            deleted = await purge_old_metrics(cfg.monitoring.metrics_retention_days)
            if deleted:
                log.info("metrics_purged", rows=deleted)
        except Exception as e:
            log.error("maintenance_error", exc_info=e)

        # Sleep 24 hours (or until stop)
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.ensure_future(stop_event.wait())),
                timeout=86400,
            )
            break
        except asyncio.TimeoutError:
            pass

    log.info("maintenance_loop_stopped")


# ── Scan trigger ──────────────────────────────────────────────────────────────

class ScanTriggerEvent:
    """
    Wraps an asyncio.Event with auto-reset semantics.
    When set, the reasoning loop runs immediately then clears.
    """
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def set(self) -> None:
        self._event.set()

    async def wait_or_timeout(self, timeout: float) -> bool:
        """Returns True if triggered manually, False if timed out."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            self._event.clear()
            return True
        except asyncio.TimeoutError:
            return False


# ── Main orchestrator ──────────────────────────────────────────────────────────

async def run_service() -> None:
    """
    Start all background tasks and the FastAPI server.
    Runs until SIGTERM/SIGINT.
    """
    log.info(
        "ai_guardian_starting",
        version=VERSION,
        domain=cfg.server.domain,
        host=cfg.service.host,
        port=cfg.service.port,
        dry_run=cfg.safety.dry_run,
        ai_enabled=cfg.ai.enabled,
    )

    # Initialize database
    await init_db()

    # Shared stop signal for all loops
    stop_event = asyncio.Event()

    # Scan trigger for manual /scan endpoint
    scan_trigger = ScanTriggerEvent()
    set_scan_trigger(scan_trigger)

    # ── Build FastAPI / uvicorn ────────────────────────────────────────────────
    app = create_app()

    uv_config = uvicorn.Config(
        app=app,
        host=cfg.service.host,
        port=cfg.service.port,
        log_level=cfg.service.log_level.lower(),
        access_log=False,       # structlog handles logging
        loop="asyncio",
    )
    server = uvicorn.Server(uv_config)

    # ── Signal handlers ────────────────────────────────────────────────────────
    shutdown_started = False

    def _handle_signal(sig, frame):
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        log.info("shutdown_signal_received", signal=sig)
        stop_event.set()
        server.should_exit = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── Startup notification ──────────────────────────────────────────────────
    await _notifier.notify(
        f"🟢 AI Guardian v{VERSION} started on `{cfg.server.hostname}` ({cfg.server.domain})\n"
        f"API: `http://{cfg.service.host}:{cfg.service.port}`\n"
        f"Dry-run: `{cfg.safety.dry_run}` | AI: `{cfg.ai.enabled}`",
        level="ok",
    )

    # ── Run all tasks concurrently ─────────────────────────────────────────────
    try:
        await asyncio.gather(
            # Monitor loops
            system_monitor_loop(stop_event),
            docker_monitor_loop(stop_event),
            security_monitor_loop(stop_event, ban_callback=_ban_callback, ssh_event_callback=_ssh_event_callback),
            # AI reasoning loop
            reasoning_loop(stop_event),
            # Maintenance
            maintenance_loop(stop_event),
            # Telegram two-way bot
            _telegram_bot.poll_loop(stop_event),
            # FastAPI server
            server.serve(),
            return_exceptions=True,
        )
    except Exception as e:
        log.error("fatal_error", exc_info=e)
    finally:
        stop_event.set()
        await _notifier.notify(
            f"🔴 AI Guardian stopped on `{cfg.server.hostname}`",
            level="warning",
        )
        await _notifier.close()
        log.info("ai_guardian_stopped")


# ── CLI entry point ────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Guardian — Autonomous server manager"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate all actions without executing them")
    parser.add_argument("--no-ai", action="store_true",
                        help="Disable AI reasoning (monitoring only)")
    parser.add_argument("--port", type=int, default=None,
                        help="Override API port")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to config.yaml (default: ./config.yaml)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Apply CLI overrides to config
    if args.dry_run:
        cfg.safety.dry_run = True
        log.info("dry_run_mode_enabled")
    if args.no_ai:
        cfg.ai.enabled = False
        log.info("ai_disabled_monitoring_only")
    if args.port:
        cfg.service.port = args.port

    try:
        asyncio.run(run_service())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error("startup_error", exc_info=e)
        sys.exit(1)


if __name__ == "__main__":
    main()
