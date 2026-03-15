# How This Stack Works

A plain-English explanation of every service, how they connect, and why things are set up the way they are.

---

## Architecture overview

```
Your browser
     |
     |─── http://192.168.1.83:3234 ──► open-webui (Docker container)
     |                                        |
     |                                        │ http://host.docker.internal:11434
     |                                        ▼
     |                               ollama (native systemd service)
     |                                        |
     |                               AMD GPU via ROCm
     |
     |─── http://192.168.1.83:4389 ──► docmost (Docker container)
     |                                        |
     |                              ┌─────────┴─────────┐
     |                              ▼                   ▼
     |                         postgresql             redis
     |                       (Docker container)  (Docker container)
     |
     └─── http://192.168.1.83:11457 ► glance (Docker container)
                                              |
                                     /var/run/docker.sock (read-only)
```

All Docker containers share an internal bridge network called `ai-network`. They talk to each other using container names as hostnames (e.g. `docmost_db`, `redis`). Ollama is the exception — it lives outside Docker entirely.

---

## Services

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

**Port:** `3234` on host → `8080` in container
**Data volume:** `ai-server_open_webui_data` — stores chat history, user accounts, settings

---

### Docmost — wiki / knowledge base

Docmost is a self-hosted wiki. It needs two supporting services to function:

- **PostgreSQL** stores all pages, workspaces, and user accounts in a relational database
- **Redis** handles background jobs and caching (e.g. real-time collaboration, notifications)

Docmost connects to them using the container names as hostnames — this works because all three are on the same `ai-network` bridge:
- `DATABASE_URL=postgresql://docmost:password@docmost_db:5432/docmost`
- `REDIS_URL=redis://redis:6379`

**Port:** `4389` on host → `3000` in container
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

**Port:** `11457` on host → `8080` in container

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

---

## Data persistence

Data is never stored inside containers. Containers are ephemeral — they can be deleted and recreated without losing anything.

| Storage | What it holds | Where on disk |
|---------|--------------|---------------|
| `ai-server_open_webui_data` | Chat history, user settings | `/var/lib/docker/volumes/` |
| `ai-server_docmost_data` | File uploads, attachments | `/var/lib/docker/volumes/` |
| `ai-server_postgres_data` | All Docmost documents | `/var/lib/docker/volumes/` |
| `ai-server_redis_data` | Queue and cache | `/var/lib/docker/volumes/` |
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
| `APP_URL` | docmost | Public URL for link generation |
| `APP_SECRET` | docmost | Session signing secret |
| `DATABASE_URL` | docmost | PostgreSQL connection string |
| `REDIS_URL` | docmost | Redis connection string |
| `OPEN_WEBUI_PORT` | compose | Host port for Open WebUI |
| `GLANCE_PORT` | compose | Host port for Glance |
| `DOCMOST_PORT` | compose | Host port for Docmost |

Copy `.env.example` to get started — it documents every variable.

---

See [`OPERATIONS.md`](OPERATIONS.md) for setup steps, updates, backups, and troubleshooting.
