# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

AI Guardian is an autonomous server manager for the `ai-server` Docker stack. It runs as a systemd service (`ai-guardian`), monitors host and container health via async loops, calls a local Ollama LLM for reasoning, and executes corrective actions with a configurable risk/approval model.

## Common Commands

```bash
# Run with venv (development)
cd /home/hectorsvillai/Desktop/ai-server/guardian
source venv/bin/activate
PYTHONPATH=/home/hectorsvillai/Desktop/ai-server python ai_guardian.py --dry-run --no-ai

# Run flags
python ai_guardian.py --dry-run   # simulate all actions, no changes
python ai_guardian.py --no-ai     # monitoring only, skip Ollama
python ai_guardian.py --port 9901 # override API port

# Install / reinstall
sudo bash setup.sh

# Systemd service
sudo systemctl status ai-guardian
sudo systemctl restart ai-guardian
sudo journalctl -u ai-guardian -f
tail -f logs/guardian.log

# API (service must be running)
curl http://127.0.0.1:9900/status | jq
curl http://127.0.0.1:9900/health
curl -X POST http://127.0.0.1:9900/scan | jq
curl http://127.0.0.1:9900/docs   # Swagger UI

# Emergency stop via API
curl -X POST http://127.0.0.1:9900/emergency-stop \
     -H "Content-Type: application/json" \
     -d '{"stop": true, "reason": "manual"}' | jq

# Update dependencies
source venv/bin/activate
pip install --upgrade -r requirements.txt
```

## Architecture

The service is a single Python process (`ai_guardian.py`) that runs all components as concurrent asyncio tasks via `asyncio.gather()`.

### Async task structure (all run concurrently in `run_service()`)
- **`system_monitor_loop`** — polls host CPU/RAM/disk/GPU every 60s via psutil
- **`docker_monitor_loop`** — polls container status/health every 60s via docker-py
- **`security_monitor_loop`** — reads `/var/log/auth.log` every 30s; triggers `ban_callback` at SSH failure threshold
- **`reasoning_loop`** — OADAV cycle every 5 min (see below)
- **`maintenance_loop`** — daily DB metric purge
- **`_telegram_bot.poll_loop`** — two-way Telegram bot for interactive commands
- **`server.serve()`** — uvicorn/FastAPI on port 9900

### OADAV reasoning cycle (`ai/reasoning.py`)
1. **Observe** — concurrently calls `collect_system_metrics()`, `collect_docker_metrics()`, `collect_security_metrics()`, and `get_recent_events()`
2. **Analyze** — builds a structured prompt including all metrics + config summary, sends to Ollama (`llama3.1:8b` preferred, falls back to `llama3.2:3b`), expects JSON response with `summary`, `health_score`, `issues`, `actions`, `reasoning`, `confidence`
3. **Decide** — filters proposals through confidence thresholds and prohibited action list
4. **Act** — routes by `risk_level`: `low`/`medium` → auto-execute; `high`/`critical` → send Telegram/Discord approval request and wait up to 10 min
5. **Verify** — for container restarts, waits 15s then re-checks container status

### Key module roles

| Module | Role |
|--------|------|
| `core/config.py` | Pydantic config tree; `cfg` singleton imported everywhere; env vars overlay YAML |
| `core/database.py` | Async SQLite (aiosqlite, WAL mode) — stores metrics, events, decisions, actions |
| `core/logger.py` | Structlog JSON logger; `get_logger(__name__)` pattern |
| `actions/executor.py` | Safe dispatcher: dry-run gate, rate-limiting, prohibited-action enforcement, audit writes |
| `actions/docker_actions.py` | Container restart/stop/prune/pull |
| `actions/system_actions.py` | UFW ban/unban, apt update, disk cleanup |
| `ai/ollama_client.py` | Async httpx client wrapping Ollama `/api/generate` |
| `ai/prompts.py` | `SYSTEM_PROMPT`, `build_analysis_prompt()`, `build_config_summary()` |
| `api/routes.py` | FastAPI router; `set_scan_trigger()` wires the `/scan` endpoint to the reasoning loop |
| `notifications/webhook.py` | Telegram / Discord / Slack; builds approval links for high-risk actions |
| `notifications/telegram_bot.py` | Long-poll Telegram bot for two-way commands |

## Configuration

All settings are in `config.yaml`. Environment variables override YAML (see `core/config.py` → `_overlay_env()`). The `cfg` singleton is loaded at import time — changes to `config.yaml` require a service restart.

Notification credentials must be set via environment variables (not in `config.yaml`):
- `GUARDIAN_TELEGRAM_TOKEN` / `GUARDIAN_TELEGRAM_CHAT_ID`
- `GUARDIAN_DISCORD_WEBHOOK`
- `GUARDIAN_DRY_RUN`, `GUARDIAN_EMERGENCY_STOP`

Set these in the project `.env` file (gitignored, at `/home/hectorsvillai/Desktop/ai-server/.env`). The service loads it via `EnvironmentFile=` — never hardcode tokens directly in the service unit. After editing `.env`, reinstall and reload:
```bash
sudo cp ai-guardian.service /etc/systemd/system/ai-guardian.service
sudo systemctl daemon-reload && sudo systemctl restart ai-guardian
```

## Safety Rules (enforced in `actions/executor.py`)

- `prohibited_actions` list (config + hardcoded): `delete_volume`, `drop_database`, `remove_tailscale` — never executed regardless of AI output
- `safety.dry_run = true` → all actions log but never run; safe for testing
- `safety.emergency_stop = true` → halts all automated actions immediately
- Rate limits: `max_actions_per_hour` and `max_container_restarts_per_hour` per config

## Database

SQLite at `data/guardian.db` (WAL mode). Tables: `metrics`, `events`, `decisions`, `actions`. Accessed only via `core/database.py` async helpers — do not add direct SQL elsewhere.

## Import convention

The service inserts its parent directory into `sys.path` so all imports use the `guardian.*` package prefix (e.g., `from guardian.core.config import cfg`). When running directly, always set `PYTHONPATH=/home/hectorsvillai/Desktop/ai-server` or use the `venv` activated from the guardian directory.
