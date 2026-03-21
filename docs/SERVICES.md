# How This Stack Works

A plain-English explanation of every service, how they connect, and why things are set up the way they are.

---

## Architecture overview

```
Your browser (LAN or Tailscale)
     │
     │ https://webui.yourdomain.com
     │ https://wiki.yourdomain.com
     │ https://dash.yourdomain.com
     │
     │  ┌─ LAN: reaches server via LAN IP
     │  └─ Remote: reaches server via Tailscale IP (${TAILSCALE_IP})
     ▼
Caddy (ports 80 & 443, bound 0.0.0.0) ◄── TLS cert from Let's Encrypt via Cloudflare DNS challenge
     │
     ├─── reverse_proxy ──► open-webui:8080 (Docker container)
     │                             │
     │                             │ http://host.docker.internal:11434
     │                             ▼
     │                    ollama (native systemd service)
     │                             │
     │                    AMD GPU via ROCm
     │
     ├─── reverse_proxy ──► docmost:3000 (Docker container)
     │                             │
     │                   ┌─────────┴─────────┐
     │                   ▼                   ▼
     │              postgresql             redis
     │           (Docker container)  (Docker container)
     │
     └─── reverse_proxy ──► glance:8080 (Docker container)
                                   │
                          /var/run/docker.sock (read-only)
```

All Docker containers share an internal bridge network called `ai-network`. They talk to each other using container names as hostnames (e.g. `docmost_db`, `redis`). Ollama is the exception — it lives outside Docker entirely. Caddy sits in front of all services and handles HTTPS termination.

---

## Services

### Caddy — reverse proxy and HTTPS

Caddy is the HTTPS entry point for the whole stack. Every browser request for `webui.*`, `wiki.*`, or `dash.*` lands on Caddy first. Caddy decrypts it, then forwards plain HTTP to the correct container over the internal Docker network, and encrypts the response before sending it back to the browser. The other containers never deal with TLS.

**How a request flows:**

```
Browser
  │  HTTPS (port 443)
  ▼
Caddy container  ◄──── TLS cert (Let's Encrypt, stored in caddy_data volume)
  │  plain HTTP (internal ai-network)
  ├─► open-webui:8080    (for webui.yourdomain.com)
  ├─► docmost:3000       (for wiki.yourdomain.com)
  └─► glance:8080        (for dash.yourdomain.com)
```

Caddy knows the container hostnames (`open-webui`, `docmost`, `glance`) because all four containers are on the same Docker bridge network — `ai-network`. Docker's internal DNS resolves container names to their private IPs automatically. No hardcoded IPs are needed.

**How TLS certificates are obtained:**

Let's Encrypt requires proof that you own the domain before issuing a certificate. Caddy uses the **DNS-01 challenge**: it temporarily creates a `_acme-challenge` TXT record in your Cloudflare DNS zone, Let's Encrypt verifies it, and the certificate is issued. Your server never needs to be reachable from the internet — port 443 only needs to be open on your LAN.

The `CF_API_TOKEN` env var gives Caddy permission to write to your Cloudflare DNS zone for this purpose only.

**The Caddyfile:**

```
(cloudflare_tls) {
    tls {
        dns cloudflare {env.CF_API_TOKEN}
        resolvers 1.1.1.1 8.8.8.8
        propagation_timeout 5m
    }
}

webui.{$DOMAIN} {
    import cloudflare_tls
    reverse_proxy open-webui:8080
}

wiki.{$DOMAIN} {
    import cloudflare_tls
    reverse_proxy docmost:3000
}

dash.{$DOMAIN} {
    import cloudflare_tls
    reverse_proxy glance:8080
}
```

`(cloudflare_tls)` is a reusable snippet. Each virtual host imports it and adds one directive — `reverse_proxy <container>:<port>`. `{$DOMAIN}` is substituted from the `DOMAIN` environment variable at runtime.

**Custom build:**

The standard `caddy` image does not ship the Cloudflare DNS plugin. `caddy.Dockerfile` uses `xcaddy` to compile a custom binary with it included:

```dockerfile
FROM caddy:builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:latest
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```

Run `docker compose build caddy` once before first start. The image is cached after that.

**Ports:** `80` (HTTP → HTTPS redirect), `443` (HTTPS), `443/udp` (HTTP/3)

**Data volumes:**
- `caddy_data` — TLS certificates and ACME state. Persists across restarts so certs are not re-requested every time.
- `caddy_config` — Caddy's runtime config cache.

**Environment variables needed in `.env`:**
- `DOMAIN` — your root domain (e.g. `yourdomain.com`)
- `CF_API_TOKEN` — Cloudflare API token with "Edit zone DNS" permission

See [`https-setup.md`](https-setup.md) for the full step-by-step first-time setup. See [`caddy.md`](caddy.md) for day-to-day operations and Caddyfile reference.

---

### Ollama — native host service

Ollama is the LLM runtime. It loads AI models onto the GPU and serves them via an HTTP API on port `11434`.

**Why it runs natively instead of in Docker:**
- AMD ROCm GPU drivers must match exactly between host and container — mismatches silently fall back to CPU
- Native gets direct, zero-overhead access to `/dev/kfd` and `/dev/dri`
- No GPU device passthrough or group ID mapping required

**How it's managed:**
```bash
sudo systemctl start ollama
sudo systemctl stop ollama
sudo systemctl status ollama
sudo journalctl -u ollama -f   # live logs
```

**Where models are stored:** `~/.ollama` on the host filesystem.

**How to add a model:**
```bash
ollama pull deepseek-r1
ollama list
```

---

### Open WebUI — chat interface

Open WebUI is the browser-based chat front-end. It sends your messages to Ollama and streams the responses back.

**How it connects to Ollama:**

By default, Docker containers cannot reach `localhost` on the host — `localhost` inside a container means the container itself, not the machine it runs on. Docker provides a special hostname `host.docker.internal` that resolves to the host's gateway IP (`172.17.0.1`).

For this to work, two things must be true:

1. The container must have `host.docker.internal` mapped — done in `docker-compose.yml`:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```

2. Ollama must listen on `0.0.0.0` (all interfaces), not just `127.0.0.1` (loopback only). By default Ollama only listens on loopback, so Docker cannot reach it. We fixed this by creating a systemd override:
   ```
   /etc/systemd/system/ollama.service.d/override.conf
   ```
   containing:
   ```ini
   [Service]
   Environment="OLLAMA_HOST=0.0.0.0"
   ```
   Then reloaded and restarted:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```

The environment variable `OLLAMA_BASE_URL=http://host.docker.internal:11434` in `.env` tells Open WebUI where to find Ollama.

**Port:** `127.0.0.1:3234` on host → `8080` in container (localhost-only; external access via Caddy)
**Data volume:** `ai-server_open_webui_data` — stores chat history, user accounts, settings

---

### Docmost — wiki / knowledge base

Docmost is a self-hosted wiki. It needs two supporting services to function:

- **PostgreSQL** stores all pages, workspaces, and user accounts in a relational database
- **Redis** handles background jobs and caching (e.g. real-time collaboration, notifications)

Docmost connects to them using the container names as hostnames — this works because all three are on the same `ai-network` bridge:
- `DATABASE_URL=postgresql://docmost:password@docmost_db:5432/docmost`
- `REDIS_URL=redis://redis:6379`

**Port:** `127.0.0.1:4389` on host → `3000` in container (localhost-only; external access via Caddy)
**Data volumes:**
- `ai-server_docmost_data` — uploaded files and attachments
- `ai-server_postgres_data` — all document content
- `ai-server_redis_data` — queue and cache

---

### Glance — dashboard

Glance is a lightweight homepage dashboard. It shows bookmarks, system stats, RSS feeds, and a clock.

It mounts the Docker socket read-only (`/var/run/docker.sock`) so it can read metadata about running containers to display in widgets.

Configuration lives in `./config/glance.yml` in this repo — mounted into the container at `/app/config/glance.yml`. Glance auto-reloads when this file changes.

See [`GLANCE_GUIDE.md`](GLANCE_GUIDE.md) for widget reference and customization.

**Port:** `127.0.0.1:11457` on host → `8080` in container (localhost-only; external access via Caddy)

---

## Networking

All containers are attached to a custom Docker bridge network named `ai-network`:

```yaml
networks:
  ai-network:
    driver: bridge
```

On a bridge network, containers can reach each other by their service name. For example, Docmost can connect to `docmost_db:5432` without knowing any IP address. Docker handles the DNS resolution internally.

Ollama sits outside this network on the host. Containers reach it via `host.docker.internal`, which Docker maps to the host's bridge gateway IP.

### Firewall (UFW)

Docker bypasses UFW by writing iptables rules directly. To prevent services from being reachable at `IP:port` from the LAN, all service ports are bound to `127.0.0.1` in `docker-compose.yml` rather than `0.0.0.0`. Caddy routes to them via the internal Docker network using container names — host ports are not involved.

Active UFW rules:
- `22195/tcp` — SSH (open to all)
- `80/tcp`, `443/tcp` — Caddy HTTP/HTTPS (open to all)
- `11434/tcp` — Ollama, allowed only from `172.19.0.0/16` (the `ai-network` Docker subnet)
- `40404/tcp` — rocm-stats, allowed only from `172.19.0.0/16`

All other ports are closed. See [`UFW.md`](UFW.md) for the full guide.

### Tailscale

Tailscale is installed on this server as a systemd service. It creates a private WireGuard overlay network so client devices (laptop, phone) can reach the server from anywhere.

Cloudflare DNS A records for `webui`, `wiki`, and `dash` point to the server's Tailscale IP (`${TAILSCALE_IP}` in `.env`). Caddy listens on `0.0.0.0:443`, so it accepts connections arriving over the Tailscale interface with no config changes. No extra UFW rule is needed — port 443 is already open.

See [`TAILSCALE.md`](TAILSCALE.md) for setup, DNS configuration, and troubleshooting.

---

## Data persistence

Data is never stored inside containers. Containers are ephemeral — they can be deleted and recreated without losing anything.

| Storage | What it holds | Where on disk |
|---------|--------------|---------------|
| `ai-server_open_webui_data` | Chat history, user settings | `/var/lib/docker/volumes/` |
| `ai-server_docmost_data` | File uploads, attachments | `/var/lib/docker/volumes/` |
| `ai-server_postgres_data` | All Docmost documents | `/var/lib/docker/volumes/` |
| `ai-server_redis_data` | Queue and cache | `/var/lib/docker/volumes/` |
| `ai-server_caddy_data` | TLS certificates, ACME state | `/var/lib/docker/volumes/` |
| `ai-server_caddy_config` | Caddy runtime config cache | `/var/lib/docker/volumes/` |
| `~/.ollama` | Downloaded models | Host home directory |
| `~/.credentials/ai-server.txt` | Service passwords | Host home directory |

All containers use `restart: unless-stopped` — they come back automatically after a reboot. Ollama auto-starts via systemd.

---

## Environment variables

All configuration is driven by `.env` in the repo root (gitignored). `docker-compose.yml` reads from it via `${VARIABLE}` substitution.

Key variables:

| Variable | Used by | Purpose |
|----------|---------|---------|
| `OLLAMA_BASE_URL` | open-webui | Where to find the Ollama API |
| `APP_URL` | docmost | Public URL for link generation (use HTTPS URL if Caddy is set up) |
| `APP_SECRET` | docmost | Session signing secret |
| `DATABASE_URL` | docmost | PostgreSQL connection string |
| `REDIS_URL` | docmost | Redis connection string |
| `OPEN_WEBUI_PORT` | compose | Host port for Open WebUI (direct access) |
| `GLANCE_PORT` | compose | Host port for Glance (direct access) |
| `DOCMOST_PORT` | compose | Host port for Docmost (direct access) |
| `DOMAIN` | caddy | Root domain (e.g. `yourdomain.com`) |
| `CF_API_TOKEN` | caddy | Cloudflare API token for DNS-01 ACME challenge |
| `TAILSCALE_IP` | reference | Server's Tailscale IPv4 — set as Cloudflare DNS A records for remote access |
| `BACKUP_DEST` | backup.sh | External drive path for backups |

Copy `.env.example` to get started — it documents every variable.

---

See [`OPERATIONS.md`](OPERATIONS.md) for setup steps, updates, backups, and troubleshooting. See [`https-setup.md`](https-setup.md) for Caddy + HTTPS setup.
