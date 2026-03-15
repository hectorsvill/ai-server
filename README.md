# AI Server

Self-hosted AI stack running on `192.168.1.83`.

## Services

| Service | URL | Purpose |
|---------|-----|---------|
| Open WebUI | http://192.168.1.83:3234 | Chat UI for LLMs |
| Docmost | http://192.168.1.83:4389 | Wiki / knowledge base |
| Glance | http://192.168.1.83:11457 | Dashboard |
| Ollama | http://192.168.1.83:11434 | LLM runtime (native, not Docker) |

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

- Docker named volumes (`/var/lib/docker/volumes/`) store Open WebUI, Docmost, PostgreSQL, and Redis data
- Ollama models live in `~/.ollama` on the host
- Containers restart automatically (`restart: unless-stopped`)
- Ollama auto-starts via systemd on boot

## Documentation

- [`SERVICES.md`](SERVICES.md) — service roles, ports, volumes, and architecture
- [`OPERATIONS.md`](OPERATIONS.md) — setup, maintenance, backup, and troubleshooting
- [`CREDENTIALS.md`](CREDENTIALS.md) — credential storage and password reset procedures
- [`GLANCE_GUIDE.md`](GLANCE_GUIDE.md) — Glance dashboard configuration

## Prerequisites

- Docker Engine + Compose plugin
- AMD GPU with ROCm drivers
  - https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/quick-start.html
- Native Ollama install: `curl -fsSL https://ollama.com/install.sh | sh`

## Security

- Secrets live in `.env` (gitignored) — never commit it
- Service credentials are stored in `~/.credentials/ai-server.txt` (outside this repo)
- See [`CREDENTIALS.md`](CREDENTIALS.md) for password reset procedures
