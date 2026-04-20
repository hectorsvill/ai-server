# AI Server

Self-hosted AI stack on a local machine with an AMD GPU. Ollama runs natively for direct ROCm GPU access. All other services run in Docker Compose. Caddy handles HTTPS. Tailscale provides remote access from anywhere. AI Guardian monitors the entire stack 24/7 and uses a local LLM to reason about health and take corrective action.

## Architecture

```
Browser / Tailscale client
        ↓ HTTPS (443)
   Caddy (reverse proxy + Cloudflare TLS)
        ├── webui.DOMAIN   → open-webui:8080
        ├── docs.DOMAIN    → docmost:3000
        ├── n8n.DOMAIN     → n8n:5678
        └── dash.DOMAIN    → glance:8080
                                └── host:40404 (rocm-stats)

Host (native systemd services)
  ├── ollama          — LLM runtime, AMD ROCm GPU, port 11434
  ├── rocm-stats      — GPU metrics HTTP server, port 40404
  └── ai-guardian     — autonomous server manager, port 9900
            ↓ monitors everything above
        Telegram (alerts + two-way control)
```

All Docker service ports are bound to `127.0.0.1`. Only Caddy exposes 80/443 to the network.

## Services

| Service | Purpose | Access |
|---------|---------|--------|
| Open WebUI | Chat UI for Ollama models | `https://webui.DOMAIN` |
| Docmost | Docs / knowledge base | `https://docs.DOMAIN` |
| n8n | Workflow automation | `https://n8n.DOMAIN` |
| Glance | System dashboard | `https://dash.DOMAIN` |
| Ollama | LLM runtime (native, not Docker) | `localhost:11434` (server only) |
| Caddy | Reverse proxy + HTTPS termination | ports 80 & 443 |
| AI Guardian | Autonomous stack monitor + manager | `localhost:9900` (server only) |

## Quick start

```bash
# 1. Copy and fill in secrets
cp .env.example .env

# 2. Start all Docker services
docker compose up -d

# 3. Start Ollama (native systemd)
sudo systemctl enable --now ollama

# 4. Pull a model
ollama pull llama3.1:8b

# 5. Install AI Guardian
cd guardian && sudo bash setup.sh
```

## AI Guardian

Guardian is an autonomous server manager that runs as a systemd service alongside the stack. It monitors host resources, Docker containers, and security events, then uses the local Ollama LLM to reason about what is happening and take action.

**What it does:**

- Monitors CPU, RAM, disk, GPU (AMD RX 7900 GRE), and Docker containers every 60 seconds
- Reads `/var/log/auth.log` every 30 seconds for SSH brute-force attempts
- Runs an AI reasoning cycle (OADAV) every 5 minutes using `llama3.1:8b`
- Auto-executes low-risk actions (container restarts, disk cleanup)
- Sends a Telegram approval request for high/critical-risk actions and waits for your response
- Sends an immediate Telegram alert on every SSH login and logout
- Auto-bans IPs that exceed the SSH failure threshold via UFW
- Keeps a full audit trail of every decision and action in SQLite (`guardian/data/guardian.db`)

**Telegram bot commands** (message your bot from anywhere):

| Command | Action |
|---------|--------|
| `/status` | Health snapshot — score, containers, CPU/RAM/GPU |
| `/events` | Recent alerts and anomalies |
| `/decisions` | Last AI reasoning cycle output |
| `/scan` | Trigger an immediate reasoning cycle |
| `/emergencystop` | Halt all automated actions |
| `/help` | List commands |

**Managing the service:**

```bash
sudo systemctl status ai-guardian
sudo systemctl restart ai-guardian
sudo journalctl -u ai-guardian -f

# Query the local API directly
curl http://127.0.0.1:9900/status | jq
curl http://127.0.0.1:9900/health
curl -X POST http://127.0.0.1:9900/scan | jq

# Emergency stop via API
curl -X POST http://127.0.0.1:9900/emergency-stop \
     -H "Content-Type: application/json" \
     -d '{"stop": true, "reason": "manual"}' | jq
```

**Safety model:**

- Actions marked `high` or `critical` risk always require Telegram approval — Guardian never executes them autonomously
- `delete_volume`, `drop_database`, and `remove_tailscale` are hardcoded prohibited actions regardless of AI output
- Set `GUARDIAN_DRY_RUN=true` in `.env` to simulate all actions without making any changes
- Tailscale IPs and `127.0.0.1` are whitelisted from SSH auto-ban

See [`guardian/README.md`](guardian/README.md) for full documentation.

## Remote access (Tailscale)

Tailscale is installed on this server. All HTTPS services are reachable from any device on the tailnet without port forwarding. DNS A records for `webui`, `docs`, `dash`, and `n8n` point to the server's Tailscale IP (`TAILSCALE_IP` in `.env`).

See [`docs/TAILSCALE.md`](docs/TAILSCALE.md) for setup and DNS configuration.

## Backups

```bash
bash tools/backup.sh
```

Backs up PostgreSQL, five Docker volumes, and repo config files to an external drive (`BACKUP_DEST` in `.env`). Keeps the last 7 dated backups. Guardian's SQLite database (`guardian/data/`) is excluded from Docker volume backups — add it to `backup.sh` if you want audit history preserved.

See [`docs/BACKUP.md`](docs/BACKUP.md).

## Data persistence

- Docker named volumes: Open WebUI, Docmost, PostgreSQL, Redis, Caddy TLS certs
- Ollama models: `~/.ollama` on the host
- Guardian audit trail: `guardian/data/guardian.db` (SQLite, 30-day retention)
- All containers use `restart: unless-stopped`
- Ollama, rocm-stats, and ai-guardian auto-start via systemd on boot

## Security

- Secrets in `.env` (gitignored) — never commit it; keep a copy at `~/.credentials/ai-server.txt`
- All service ports bound to `127.0.0.1`; only Caddy (80/443) is network-accessible
- Remote access via Tailscale — no port forwarding, no public IP required
- Docker bypasses UFW — service ports are protected by the `127.0.0.1` bind, not firewall rules
- AI Guardian auto-bans SSH brute-force IPs via UFW and alerts on every login
- Guardian Telegram credentials (`GUARDIAN_TELEGRAM_TOKEN`, `GUARDIAN_TELEGRAM_CHAT_ID`) are set in `.env` — never in `guardian/config.yaml`

See [`docs/UFW.md`](docs/UFW.md) and [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md).

## Prerequisites

- Docker Engine + Compose plugin
- AMD GPU with ROCm drivers — [quick-start guide](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html)
- Native Ollama: `curl -fsSL https://ollama.com/install.sh | sh`
- Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`
- Python 3.11+ (for Guardian and rocm-stats)

## Tools

| Script | Purpose |
|--------|---------|
| `tools/backup.sh` | Back up all data to external drive |
| `tools/update_ollama_models.sh` | Re-pull all locally installed Ollama models |
| `tools/rocm-stats.py` | GPU metrics HTTP server (systemd service, port 40404) |
| `tools/delete_all_docker_containers.py` | Force-remove all containers and volumes (destructive) |
| `guardian/setup.sh` | Install and start AI Guardian as a systemd service |

## Documentation

| File | Contents |
|------|----------|
| [`guardian/README.md`](guardian/README.md) | AI Guardian full reference — install, API, Telegram bot, config |
| [`docs/SERVICES.md`](docs/SERVICES.md) | Service roles, ports, volumes, and architecture |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Setup, maintenance, and troubleshooting |
| [`docs/TAILSCALE.md`](docs/TAILSCALE.md) | Remote access via Tailscale |
| [`docs/BACKUP.md`](docs/BACKUP.md) | Backup script, restore procedures, rotation |
| [`docs/https-setup.md`](docs/https-setup.md) | HTTPS first-time setup via Caddy + Cloudflare DNS |
| [`docs/caddy.md`](docs/caddy.md) | Caddyfile reference, cert lifecycle, adding services |
| [`docs/UFW.md`](docs/UFW.md) | Firewall rules and Docker bypass problem |
| [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md) | Credential storage and password reset procedures |
| [`docs/GLANCE_GUIDE.md`](docs/GLANCE_GUIDE.md) | Glance dashboard widget reference |
