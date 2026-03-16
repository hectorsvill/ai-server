# AI Server

Self-hosted AI stack running on your local server.

## Services

| Service | Direct URL | HTTPS URL (via Caddy) | Purpose |
|---------|------------|----------------------|---------|
| Open WebUI | http://YOUR_SERVER_IP:3234 | https://webui.yourdomain.com | Chat UI for LLMs |
| Docmost | http://YOUR_SERVER_IP:4389 | https://wiki.yourdomain.com | Wiki / knowledge base |
| Glance | http://YOUR_SERVER_IP:11457 | https://dash.yourdomain.com | Dashboard |
| Ollama | http://YOUR_SERVER_IP:11434 | — | LLM runtime (native, not Docker) |
| Caddy | — | Handles ports 80 & 443 | Reverse proxy + HTTPS termination |

## Quick start

```bash
# Start all containers
docker compose up -d

# Check status
docker compose ps
```

Ollama runs as a native systemd service — managed separately from Docker:

```bash
sudo systemctl start ollama
sudo systemctl status ollama
```

## Pull a model

```bash
ollama pull deepseek-r1
```

## Data persistence

All data survives reboots and container restarts:

- Docker named volumes (`/var/lib/docker/volumes/`) store Open WebUI, Docmost, PostgreSQL, Redis, and Caddy TLS cert data
- Ollama models live in `~/.ollama` on the host
- Containers restart automatically (`restart: unless-stopped`)
- Ollama auto-starts via systemd on boot

## Documentation

All docs live in the [`docs/`](docs/) folder:

- [`docs/SERVICES.md`](docs/SERVICES.md) — service roles, ports, volumes, and architecture
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — setup, maintenance, backup, and troubleshooting
- [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md) — credential storage and password reset procedures
- [`docs/GLANCE_GUIDE.md`](docs/GLANCE_GUIDE.md) — Glance dashboard configuration
- [`docs/https-setup.md`](docs/https-setup.md) — HTTPS first-time setup via Caddy + Cloudflare DNS
- [`docs/caddy.md`](docs/caddy.md) — Caddy operational reference: Caddyfile, cert lifecycle, adding services

## Prerequisites

- Docker Engine + Compose plugin
- AMD GPU with ROCm drivers
  - https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html
- Native Ollama install: `curl -fsSL https://ollama.com/install.sh | sh`

## Tools

Utility scripts live in [`tools/`](tools/):

- `tools/update_ollama_models.sh` — re-pulls all locally installed Ollama models (run manually to update)
- `tools/delete_all_docker_containers.py` — force-removes all Docker containers and their volumes (destructive, prompts for confirmation)

## Security

- Secrets live in `.env` (gitignored) — never commit it
- Service credentials are stored in `~/.credentials/ai-server.txt` (outside this repo)
- See [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md) for password reset procedures
- HTTPS provided by Caddy with Let's Encrypt certs via Cloudflare DNS — see [`docs/https-setup.md`](docs/https-setup.md)
