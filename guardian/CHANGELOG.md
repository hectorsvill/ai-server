# AI Guardian — Changelog

---

## v1.0.3 — 2026-04-06

### Added
- **AMD GPU monitoring** (`monitors/system.py`)
  - Runs `rocm-smi` in the thread executor on every system metrics cycle (same tool as `tools/rocm-stats.py`)
  - Parses GPU[0] (RX 7900 GRE): utilisation %, VRAM %, junction temp °C, edge temp °C, power W / max W, shader clock
  - Stored inside the `system` metric snapshot under the `gpu` key
  - Threshold alerts in `config.yaml`: GPU use (warn 90% / crit 99%), VRAM (warn 80% / crit 95%), junction temp (warn 85°C / crit 100°C)
  - GPU fields added to the AI analysis prompt so the LLM sees GPU state during reasoning
  - GPU stats block added to the Telegram `/status` command with colour-coded icons

---

## v1.0.2 — 2026-04-06

### Added
- **Two-way Telegram bot** (`notifications/telegram_bot.py`)
  - Long-polls Telegram for commands, responds with live Guardian data
  - Only responds to the configured `GUARDIAN_TELEGRAM_CHAT_ID` (unauthorized chats are ignored)
  - Commands: `/status`, `/events`, `/actions`, `/pending`, `/approve <token>`, `/deny <token>`, `/stop`, `/resume`, `/help`
  - Wired into `ai_guardian.py` as a concurrent async loop alongside all other loops

---

## v1.0.1 — 2026-04-05

### Fixed
- **Disk monitor snap spam** (`monitors/system.py`)
  - Snap packages mount as read-only squashfs at 100% full by design — they were flooding the event log with false "Disk High" warnings.
  - Added filesystem type blocklist: `squashfs`, `tmpfs`, `devtmpfs`, `overlay`, and other pseudo-filesystems are now skipped entirely.
  - Added mount prefix blocklist: `/snap/`, `/proc`, `/sys`, `/dev`, `/run/user`.
  - Only real block devices (e.g. `/`, `/boot`, external drives) trigger disk alerts.

- **Systemd unit `StartLimitIntervalSec` wrong section** (`ai-guardian.service`)
  - `StartLimitIntervalSec=300` and `StartLimitBurst=5` were placed under `[Service]`, which systemd does not recognize there (logged as "Unknown key name").
  - Moved both keys to `[Unit]` where they are valid per the systemd spec.

---

## v1.0.0 — 2026-04-05

### Initial release
- Full async service: asyncio + FastAPI on port 9900
- Three monitor loops: system (60s), Docker (60s), security (30s)
- AI reasoning loop (OADAV) every 5 min via Ollama (`llama3.1:8b` / `llama3.2:3b`)
- aiosqlite WAL database: metrics, events, decisions, actions audit trail
- structlog JSON rotating logger
- Action executor with dry-run, rate limiting, confidence thresholds, prohibited action list
- High/critical risk actions require human approval via Telegram/Discord webhook
- SSH brute-force detection + auto UFW ban with auto-unban
- Docker actions: restart, stop, pull, prune
- System actions: disk cleanup, apt security updates, UFW management
- FastAPI control plane: 16 endpoints including approve/deny with browser-clickable links
- Telegram, Discord, Slack notification support
- Systemd service with graceful shutdown
- Setup script (`setup.sh`) with virtualenv creation and service installation
