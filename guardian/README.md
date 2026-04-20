# AI Guardian

Autonomous server manager for **vailab.us**.  
Runs 24/7 as a systemd service, monitoring the entire `ai-server` Docker stack and host system, then using a local LLM (via Ollama) to reason about health and take corrective action.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         AI Guardian (port 9900)                         │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ System       │  │ Docker       │  │ Security     │  ← Monitors       │
│  │ Monitor      │  │ Monitor      │  │ Monitor      │    (async loops)  │
│  │ (60s)        │  │ (60s)        │  │ (30s)        │                  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                  │
│         └─────────────────┼─────────────────┘                          │
│                           ▼                                             │
│               ┌───────────────────────┐                                │
│               │  AI Reasoning Loop    │  ← OADAV every 5 min           │
│               │  (Ollama LLM)         │    Observe→Analyze→Decide      │
│               └───────────┬───────────┘    →Act→Verify                 │
│                           ▼                                             │
│               ┌───────────────────────┐                                │
│               │   Action Executor     │  ← dry-run, rate-limit,        │
│               │                       │    risk routing, audit trail   │
│               └───────────┬───────────┘                                │
│                           ▼                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ Docker       │  │ System       │  │ Notification │  ← Action impls  │
│  │ Actions      │  │ Actions      │  │ (Telegram/   │                  │
│  │              │  │ (UFW, apt)   │  │  Discord)    │                  │
│  └──────────────┘  └──────────────┘  └──────────────┘                  │
│                                                                         │
│  ┌─────────────────────────────────────────────┐                       │
│  │  FastAPI REST API  (http://127.0.0.1:9900)  │  ← Control plane      │
│  │  /health  /status  /events  /actions  ...   │                       │
│  └─────────────────────────────────────────────┘                       │
│                                                                         │
│  ┌─────────────────────────────────────────────┐                       │
│  │  SQLite (WAL) — guardian.db                 │  ← Audit trail        │
│  │  metrics / events / decisions / actions     │                       │
│  └─────────────────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Managed Services

| Service | Type | Critical | Port |
|---------|------|----------|------|
| caddy | Docker | ✅ | 80, 443 |
| open-webui | Docker | | 3234 |
| docmost | Docker | | 4389 |
| docmost_db | Docker | | — |
| redis | Docker | | — |
| n8n | Docker | | 5679 |
| glance | Docker | | 11457 |
| ollama | Native (systemd) | ✅ | 11434 |
| rocm-stats | Native (systemd) | | 40404 |
| tailscaled | Native (systemd) | ✅ | — |

---

## Quick Start

### Prerequisites

```bash
# Docker + Compose (already installed)
# Python 3.11+
python3 --version

# Ollama running with at least one model
ollama list
# If empty, pull a model:
ollama pull llama3.2:3b      # fast, 2GB
ollama pull llama3.1:8b      # deeper reasoning, 5GB
```

### Install

```bash
cd /home/hectorsvillai/Desktop/ai-server/guardian
sudo bash setup.sh
```

This will:
1. Create a Python virtualenv at `guardian/venv/`
2. Install all dependencies from `requirements.txt`
3. Create `data/` and `logs/` directories
4. Install the systemd service to `/etc/systemd/system/ai-guardian.service`
5. Run a config validation test

### Start

```bash
sudo systemctl enable --now ai-guardian

# Check status
sudo systemctl status ai-guardian

# Tail logs
tail -f /home/hectorsvillai/Desktop/ai-server/guardian/logs/guardian.log

# Or via journald
sudo journalctl -u ai-guardian -f
```

### API Dashboard

```
http://127.0.0.1:9900/docs    # Interactive Swagger UI
http://127.0.0.1:9900/status  # JSON status snapshot
```

---

## Configuration

All settings live in `config.yaml`.  
Environment variables override YAML values (see `core/config.py`).

### Key Settings

```yaml
safety:
  dry_run: false          # Set true to simulate all actions (safe for testing)
  emergency_stop: false   # Set true to halt all automated actions immediately

ai:
  enabled: true
  model: "llama3.2:3b"           # Fast model for routine checks
  reasoning_model: "llama3.1:8b" # Heavier model for analysis

notifications:
  enabled: false          # Set true and configure telegram/discord below
  telegram:
    enabled: true
    # Set via env: GUARDIAN_TELEGRAM_TOKEN and GUARDIAN_TELEGRAM_CHAT_ID
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `GUARDIAN_TELEGRAM_TOKEN` | Telegram bot token |
| `GUARDIAN_TELEGRAM_CHAT_ID` | Telegram chat ID to send to |
| `GUARDIAN_DISCORD_WEBHOOK` | Discord webhook URL |
| `GUARDIAN_SLACK_WEBHOOK` | Slack webhook URL |
| `GUARDIAN_DRY_RUN` | `true` = dry-run mode |
| `GUARDIAN_EMERGENCY_STOP` | `true` = halt all actions |
| `GUARDIAN_OLLAMA_URL` | Override Ollama URL |
| `GUARDIAN_AI_MODEL` | Override AI model name |

Set env vars in the project `.env` file (gitignored, at the repo root). The service loads it via `EnvironmentFile=` — never hardcode tokens in the service unit file. After editing `.env`, run:
```bash
sudo cp ai-guardian.service /etc/systemd/system/ai-guardian.service
sudo systemctl daemon-reload && sudo systemctl restart ai-guardian
```

---

## API Reference

### Core Endpoints

```
GET  /health                  Liveness probe
GET  /status                  Full system snapshot
GET  /metrics/latest?type=    Latest metrics (system|docker|security)
GET  /events?severity=        Event history with filtering
GET  /decisions               AI decision history
GET  /actions                 Action history
GET  /actions/pending         Actions awaiting human approval
GET  /logs?lines=100          Tail log file
GET  /config                  View active config
```

### Control Endpoints

```
POST /scan                    Trigger immediate AI analysis
POST /emergency-stop          { "stop": true, "reason": "..." }
POST /actions/approve/{token} Approve a pending action
POST /actions/deny/{token}    Deny a pending action
GET  /actions/approve/{token} Browser-clickable approval link (for Telegram)
GET  /actions/deny/{token}    Browser-clickable deny link
```

---

## The OADAV Reasoning Loop

Every 5 minutes (configurable), the AI Guardian runs one cycle:

### 1. Observe
Collects concurrently from all three monitors:
- Host: CPU, RAM, disk usage, load average, top processes
- Docker: container status/health, restart counts, resource usage, log errors
- Security: SSH failure IPs, UFW blocks, suspicious processes, exposed ports

### 2. Analyze
Builds a detailed prompt including:
- All observed metrics
- Recent unresolved events
- Summary of the actual `docker-compose.yml`, `Caddyfile`, `.env` keys
- Server identity (hostname, domain, Tailscale IP)

Sends to Ollama (prefers `llama3.1:8b` for reasoning, falls back to `llama3.2:3b`).

**Example analysis output from the AI:**
```json
{
  "summary": "Server healthy; open-webui has 3 restarts, Redis CPU spike noted",
  "health_score": 82,
  "issues": [
    {
      "severity": "warning",
      "category": "docker",
      "title": "open-webui restart count elevated",
      "description": "Container has restarted 3 times in the past hour. Log errors suggest OOM."
    }
  ],
  "actions": [
    {
      "action_type": "restart_container",
      "target": "open-webui",
      "reason": "Restart to clear potential memory leak, OOM evident in logs",
      "risk_level": "low",
      "confidence": 0.92,
      "parameters": {}
    }
  ],
  "reasoning": "open-webui logs show 'Killed' entries consistent with OOM. Restart is safe given restart_policy=always and Ollama models are loaded separately.",
  "confidence": 0.90
}
```

### 3. Decide
Filters proposals through safety rules:
- Checks confidence thresholds (low risk: ≥90%, medium: ≥97%)
- Blocks prohibited actions (`delete_volume`, `drop_database`, etc.)
- Respects emergency_stop flag

### 4. Act
Routes by risk level:
- `low` → **auto-execute**
- `medium` → **auto-execute with prominent log entry**
- `high` / `critical` → **send Telegram/Discord approval request, wait up to 10 min**

All actions are written to the SQLite audit trail with full parameters and outcome.

### 5. Verify
For container restarts: waits 15 seconds, then re-checks container status.  
If the container is still not running → creates a `warning` event.

---

## Security Model

### What the Guardian Can Do Autonomously (Low Risk)
- Restart Docker containers
- Pull updated images
- Prune stopped containers, dangling images, unused networks
- Clean disk space (journal, apt cache, old logs)
- Check service health

### What Requires Human Approval (High/Critical Risk)
- Ban an IP via UFW
- Apply security updates (`apt-get upgrade`)
- Restart native systemd services (ollama, rocm-stats)
- Any action with `risk_level: high` or `critical`

### What Is NEVER Automated (Prohibited)
- Delete Docker volumes (`delete_volume`)
- Drop databases (`drop_database`)
- Remove Tailscale (`remove_tailscale`)

These are enforced in `actions/executor.py` and cannot be overridden via config.

### SSH Brute-Force Protection
- Security monitor reads `/var/log/auth.log` every 30 seconds
- Tracks failure counts per IP in a 10-minute rolling window
- At 20 failures: auto-ban via `ufw deny from <ip>` (configurable)
- Tailscale IP range (100.64.0.0/10) is always whitelisted
- Auto-unban after configurable duration (default: 60 min)

---

## Troubleshooting

### Service won't start
```bash
sudo systemctl status ai-guardian
sudo journalctl -u ai-guardian --since "5 minutes ago"
```

### Ollama not found
```bash
# Check Ollama is running
systemctl status ollama
curl http://127.0.0.1:11434/api/tags

# Pull a model if empty
ollama pull llama3.2:3b
```

### Docker socket permission denied
The service runs as root. If you change the `User=` in the service file, add the user to the `docker` group:
```bash
sudo usermod -aG docker yourusername
```

### Test in dry-run mode (safe, no changes)
```bash
cd /home/hectorsvillai/Desktop/ai-server/guardian
PYTHONPATH=/home/hectorsvillai/Desktop/ai-server \
    ./venv/bin/python ai_guardian.py --dry-run --no-ai
```

### Run a manual scan via API
```bash
curl -s -X POST http://127.0.0.1:9900/scan | jq
```

### Emergency stop via API
```bash
curl -s -X POST http://127.0.0.1:9900/emergency-stop \
     -H "Content-Type: application/json" \
     -d '{"stop": true, "reason": "manual override"}' | jq
```

---

## File Structure

```
guardian/
├── ai_guardian.py          # Main entry point + service orchestration
├── config.yaml             # All configuration (edit this)
├── requirements.txt        # Python dependencies
├── setup.sh                # One-shot setup script
├── ai-guardian.service     # systemd unit file (loads secrets from ../.env)
├── .gitignore              # excludes venv/, data/, logs/, __pycache__/
├── data/
│   └── guardian.db         # SQLite WAL database (auto-created)
├── logs/
│   └── guardian.log        # Rotating JSON structured log
├── core/
│   ├── config.py           # Pydantic config model + YAML loader
│   ├── database.py         # Async SQLite (aiosqlite)
│   └── logger.py           # Structlog JSON logger
├── monitors/
│   ├── system.py           # Host resources (psutil)
│   ├── docker_monitor.py   # Container health (docker-py)
│   └── security.py         # auth.log, UFW, suspicious processes
├── actions/
│   ├── executor.py         # Safe dispatcher: dry-run, rate-limit, audit
│   ├── docker_actions.py   # Container restart/stop/prune/pull
│   └── system_actions.py   # UFW ban, apt update, disk cleanup
├── ai/
│   ├── reasoning.py        # OADAV loop (Observe→Analyze→Decide→Act→Verify)
│   ├── ollama_client.py    # Async Ollama HTTP client
│   └── prompts.py          # Prompt templates (server-specific)
├── api/
│   ├── routes.py           # FastAPI endpoints
│   └── models.py           # Pydantic response models
└── notifications/
    └── webhook.py          # Telegram / Discord / Slack
```

---

## Adding to Caddy (Optional)

To expose the Guardian API dashboard at `guardian.vailab.us` (Tailscale-only, no public):

```
guardian.vailab.us {
    # Only allow Tailscale subnet
    @tailscale remote_ip 100.64.0.0/10
    handle @tailscale {
        reverse_proxy 127.0.0.1:9900
    }
    respond "Forbidden" 403
}
```

Add this to `Caddyfile` and run `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

---

## Updating

```bash
cd /home/hectorsvillai/Desktop/ai-server/guardian
source venv/bin/activate
pip install --upgrade -r requirements.txt
sudo systemctl restart ai-guardian
```
