# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Self-hosted AI server stack running on a local machine with an AMD GPU. Key design choices: **Ollama runs natively on the host** (not in Docker) for direct AMD ROCm GPU access. **rocm-stats** (`tools/rocm-stats.py`) also runs natively as a systemd service to expose GPU metrics to Glance. All other services run in Docker Compose.

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

# Check rocm-stats GPU endpoint (native systemd, port 40404)
systemctl status rocm-stats
curl http://localhost:40404/health

# Monitor AMD GPU
watch -n 2 rocm-smi

# Full destructive reset (removes all containers + volumes)
python3 tools/delete_all_docker_containers.py
```

## Architecture

```
Browser (HTTPS: webui/docs/dash/n8n.{DOMAIN})
    ↓
Caddy (ports 80/443) — TLS via Cloudflare DNS-01 (no public IP needed)
    ├─→ open-webui:8080 (AI chat UI)
    │   └─→ host.docker.internal:11434 (Ollama, native host service)
    ├─→ docmost:3000 (docs)
    │   ├─→ docmost_db:5432 (PostgreSQL)
    │   └─→ redis:6379
    ├─→ n8n:5678 (workflow automation)
    └─→ glance:8080 (dashboard)
            └─→ host.docker.internal:40404 (rocm-stats, native host service)
```

**Caddy** is built from `caddy.Dockerfile` (custom build with `caddy-dns/cloudflare` plugin) because the standard image doesn't include it.

**Ollama** listens on `0.0.0.0:11434` via a systemd override at `/etc/systemd/system/ollama.service.d/override.conf`. Without this, containers cannot reach it via `host.docker.internal`.

**rocm-stats** (`tools/rocm-stats.py`) is a minimal Python HTTP server running as a systemd service on port 40404. It runs `rocm-smi` and returns GPU[0] (RX 7900 GRE) stats as HTML for Glance's `extension` widget. Systemd unit: `tools/rocm-stats.service` (installed to `/etc/systemd/system/`).

All containerized services share the `ai-network` bridge. Use container names for inter-service DNS (e.g., `docmost_db`, `redis`). Use `host.docker.internal` only to reach host-level services.

## Configuration

- **`.env`** — production secrets (gitignored). Copy from `.env.example` to create.
- **`Caddyfile`** — reverse proxy rules; uses `{$DOMAIN}` and `{$CF_API_TOKEN}` from environment.
- **`config/glance.yml`** — Glance dashboard widgets/theme; hot-reloads on save.
- **`assets/custom.css`** — cyberpunk theme for Glance.

Key environment variables: `DOMAIN`, `CF_API_TOKEN`, `POSTGRES_PASSWORD`, `APP_SECRET`, `DATABASE_URL`, `N8N_ENCRYPTION_KEY`.

## Firewall & Port Exposure

**Docker bypasses UFW.** Publishing a port in `docker-compose.yml` as `PORT:PORT` opens it to the LAN regardless of UFW rules.

To prevent direct IP:port access, all service ports are bound to `127.0.0.1`:
```yaml
ports:
  - "127.0.0.1:${PORT}:8080"   # localhost-only — LAN cannot reach this
```

Caddy routes to services via the internal `ai-network` Docker bridge using container names, so it never needs the host ports.

Native host services (Ollama, rocm-stats) must be reachable from Docker containers via `host.docker.internal`. UFW allows their ports only from the Docker subnet:
```bash
sudo ufw allow from 172.19.0.0/16 to any port 11434   # Ollama
sudo ufw allow from 172.19.0.0/16 to any port 40404   # rocm-stats
```

Active UFW rules: SSH (22195), HTTP/HTTPS (80/443), Ollama (11434) and rocm-stats (40404) from Docker subnet only.

When adding a new service: bind its port to `127.0.0.1` and add a Caddy reverse proxy block — no UFW rule needed. See `docs/UFW.md`.

## Tailscale

Tailscale is installed and running on this machine. Tailscale IP: **100.118.0.92** (hostname: `hectorsvillai-ms-7e26`).

Caddy binds ports 80/443 on `0.0.0.0`, so all HTTPS services (`webui`, `docs`, `dash`, `n8n`) are accessible over Tailscale at the same domain names — as long as DNS resolves to the Tailscale IP or the LAN IP and the machine is online.

Tailscale devices on the tailnet:
- `hectorsvillai-ms-7e26` — this server (100.118.0.92)
- `hectors-macbook-pro` — MacBook (100.64.34.72)
- `iphone-15-pro-max` — iPhone (100.65.253.93)

No UFW rule is needed for Tailscale — it uses its own encrypted overlay and the traffic hits Caddy's already-open 80/443 ports.

## Backups

`tools/backup.sh` backs up all persistent data to an external drive (`BACKUP_DEST` in `.env`, e.g. `/media/hectorsvillai/VXSLAUTH/Backups`).

```bash
# Run a manual backup
bash tools/backup.sh
```

**What is backed up:** PostgreSQL dump (`pg_dumpall`), five Docker volumes (`open_webui_data`, `docmost_data`, `caddy_data`, `caddy_config`, `redis_data`), and repo config files (`Caddyfile`, `.env`, `docker-compose.yml`, `config/`, `assets/`, `tools/`).

**Not backed up:** `ollama_data` (model weights — re-downloadable, tens of GB).

**Rotation:** keeps last 7 dated backup directories; older ones are pruned automatically.

**Automate:** add a cron entry — the script exits silently if the drive is not mounted.

```cron
0 2 * * * /bin/bash /home/hectorsvillai/Desktop/ai-server/tools/backup.sh >> /var/log/ai-server-backup.log 2>&1
```

Full restore procedures and layout details: `docs/BACKUP.md`.

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
| `docs/BACKUP.md` | Backup script usage, restore procedures, rotation |
| `docs/TAILSCALE.md` | Tailscale remote access setup, DNS config, troubleshooting |
| `docs/N8N.md` | n8n setup, integrations with Ollama/Open WebUI/Docmost, webhooks, workflow patterns |
