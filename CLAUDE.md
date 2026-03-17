# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Self-hosted AI server stack running on a local machine with an AMD GPU. Key design choice: **Ollama runs natively on the host** (not in Docker) for direct AMD ROCm GPU access. All other services run in Docker Compose.

## Common Commands

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# View logs for a specific service
docker compose logs -f caddy
docker compose logs -f open-webui

# Rebuild Caddy (required after Caddyfile changes)
docker compose build caddy && docker compose up -d caddy

# Reload Caddy config without rebuilding
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile

# Update all Ollama models
./tools/update_ollama_models.sh

# Check Ollama service status (native systemd)
systemctl status ollama
journalctl -u ollama -f

# Monitor AMD GPU
watch -n 2 rocm-smi

# Full destructive reset (removes all containers + volumes)
python3 tools/delete_all_docker_containers.py
```

## Architecture

```
Browser (HTTPS: webui/wiki/dash.{DOMAIN})
    ↓
Caddy (ports 80/443) — TLS via Cloudflare DNS-01 (no public IP needed)
    ├─→ open-webui:8080 (AI chat UI)
    │   └─→ host.docker.internal:11434 (Ollama, native host service)
    ├─→ docmost:3000 (wiki)
    │   ├─→ docmost_db:5432 (PostgreSQL)
    │   └─→ redis:6379
    └─→ glance:8080 (dashboard)
```

**Caddy** is built from `caddy.Dockerfile` (custom build with `caddy-dns/cloudflare` plugin) because the standard image doesn't include it.

**Ollama** listens on `0.0.0.0:11434` via a systemd override at `/etc/systemd/system/ollama.service.d/override.conf`. Without this, containers cannot reach it via `host.docker.internal`.

All containerized services share the `ai-network` bridge. Use container names for inter-service DNS (e.g., `docmost_db`, `redis`). Use `host.docker.internal` only to reach host-level services.

## Configuration

- **`.env`** — production secrets (gitignored). Copy from `.env.example` to create.
- **`Caddyfile`** — reverse proxy rules; uses `{$DOMAIN}` and `{$CF_API_TOKEN}` from environment.
- **`config/glance.yml`** — Glance dashboard widgets/theme; hot-reloads on save.
- **`assets/custom.css`** — cyberpunk theme for Glance.

Key environment variables: `DOMAIN`, `CF_API_TOKEN`, `POSTGRES_PASSWORD`, `APP_SECRET`, `DATABASE_URL`.

## HTTPS / TLS

Uses Cloudflare DNS-01 ACME challenge — works on a private LAN IP with no port-forwarding. DNS A records must be "DNS only" (not proxied). Certificates are stored in the `caddy_data` named volume and auto-renew ~30 days before 90-day expiry.

## Credentials & Secrets

- Production `.env` is gitignored — never commit it.
- Service credentials are kept at `~/.credentials/ai-server.txt` (outside the repo, chmod 600).
- Password reset procedures for Open WebUI (SQLite/bcrypt) and Docmost (PostgreSQL/bcrypt) are in `docs/CREDENTIALS.md`.

## Documentation

| File | Contents |
|------|----------|
| `docs/SERVICES.md` | Service architecture, networking, persistence |
| `docs/OPERATIONS.md` | Full setup, maintenance, troubleshooting |
| `docs/CREDENTIALS.md` | Password management and reset procedures |
| `docs/https-setup.md` | Step-by-step Cloudflare HTTPS setup |
| `docs/caddy.md` | Caddyfile explained, cert lifecycle, adding services |
| `docs/UFW.md` | Firewall rules, Docker bypass problem, managing ports |
| `docs/GLANCE_GUIDE.md` | Dashboard widget reference and customization |
