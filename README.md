# AI Server

Self-hosted AI stack on a local machine with an AMD GPU. Ollama runs natively for direct ROCm GPU access. All other services run in Docker Compose. Caddy handles HTTPS. Tailscale provides remote access from anywhere.

## Services

| Service | Purpose | Access |
|---------|---------|--------|
| Open WebUI | Chat UI for LLMs | `https://webui.DOMAIN` |
| Docmost | Wiki / knowledge base | `https://wiki.DOMAIN` |
| Glance | Dashboard | `https://dash.DOMAIN` |
| Ollama | LLM runtime (native systemd, not Docker) | `localhost:11434` (server only) |
| Caddy | Reverse proxy + HTTPS termination | ports 80 & 443 |

All service ports are bound to `127.0.0.1` — not reachable directly from the LAN. Access goes through Caddy over HTTPS, either from the local network or remotely via Tailscale.

## Quick start

```bash
# Copy and fill in your secrets
cp .env.example .env

# Start all containers
docker compose up -d

# Check status
docker compose ps
```

Ollama runs as a native systemd service — managed separately:

```bash
sudo systemctl start ollama
sudo systemctl status ollama
```

## Pull a model

```bash
ollama pull deepseek-r1
ollama list
```

## Remote access (Tailscale)

Tailscale is installed on this server. Client devices (MacBook, iPhone) connect via the Tailscale app to access all services from anywhere. DNS A records for `webui`, `wiki`, and `dash` point to the server's Tailscale IP (`TAILSCALE_IP` in `.env`).

See [`docs/TAILSCALE.md`](docs/TAILSCALE.md) for setup and DNS configuration.

## Backups

```bash
bash tools/backup.sh
```

Backs up PostgreSQL, five Docker volumes, and repo config files to an external drive (`BACKUP_DEST` in `.env`). Keeps the last 7 dated backups. See [`docs/BACKUP.md`](docs/BACKUP.md).

## Data persistence

All data survives reboots and container restarts:

- Docker named volumes store Open WebUI, Docmost, PostgreSQL, Redis, and Caddy TLS cert data
- Ollama models live in `~/.ollama` on the host
- Containers restart automatically (`restart: unless-stopped`)
- Ollama auto-starts via systemd on boot

## Documentation

| File | Contents |
|------|----------|
| [`docs/SERVICES.md`](docs/SERVICES.md) | Service roles, ports, volumes, and architecture |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Setup, maintenance, and troubleshooting |
| [`docs/TAILSCALE.md`](docs/TAILSCALE.md) | Remote access via Tailscale |
| [`docs/BACKUP.md`](docs/BACKUP.md) | Backup script, restore procedures, rotation |
| [`docs/https-setup.md`](docs/https-setup.md) | HTTPS first-time setup via Caddy + Cloudflare DNS |
| [`docs/caddy.md`](docs/caddy.md) | Caddyfile reference, cert lifecycle, adding services |
| [`docs/UFW.md`](docs/UFW.md) | Firewall rules and Docker bypass problem |
| [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md) | Credential storage and password reset procedures |
| [`docs/GLANCE_GUIDE.md`](docs/GLANCE_GUIDE.md) | Glance dashboard widget reference |

## Prerequisites

- Docker Engine + Compose plugin
- AMD GPU with ROCm drivers — [quick-start guide](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html)
- Native Ollama: `curl -fsSL https://ollama.com/install.sh | sh`
- Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`

## Tools

| Script | Purpose |
|--------|---------|
| `tools/backup.sh` | Back up all data to external drive |
| `tools/update_ollama_models.sh` | Re-pull all locally installed Ollama models |
| `tools/rocm-stats.py` | GPU metrics HTTP server (systemd service, port 40404) |
| `tools/delete_all_docker_containers.py` | Force-remove all containers and volumes (destructive) |

## Security

- Secrets live in `.env` (gitignored) — never commit it
- Service credentials stored in `~/.credentials/ai-server.txt` (outside this repo)
- All service ports bound to `127.0.0.1`; only Caddy (80/443) is LAN-accessible
- Remote access via Tailscale — no port forwarding, no public IP required
- See [`docs/UFW.md`](docs/UFW.md) and [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md)
