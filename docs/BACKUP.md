# Backup Guide

`tools/backup.sh` backs up all persistent ai-server data to an external drive.
Run it manually whenever you want a snapshot, or set up a cron job (see below).

---

## What gets backed up

| File/Archive | Contents |
|---|---|
| `postgres_dump.sql.gz` | Full PostgreSQL dump (all Docmost databases/roles) via `pg_dumpall` |
| `ai-server_open_webui_data.tar.gz` | Open WebUI SQLite DB, user uploads, settings |
| `ai-server_docmost_data.tar.gz` | Docmost application data |
| `ai-server_caddy_data.tar.gz` | TLS certificates and ACME account (avoids re-issuing on restore) |
| `ai-server_caddy_config.tar.gz` | Caddy runtime config cache |
| `ai-server_redis_data.tar.gz` | Redis RDB snapshot (sessions, cache) |
| `repo/` | `Caddyfile`, `.env`, `docker-compose.yml`, `config/`, `assets/`, `tools/` |

**Not backed up:** `ai-server_ollama_data` (model weights). Models are re-downloadable and can be tens of GB. Re-pull them after a restore with `ollama pull <model>`.

---

## How it works

### 1 — Preflight checks

```
DEST="/media/hectorsvillai/VXSLAUTH/Backups"
```

The script aborts immediately (`set -euo pipefail` + `die`) if:
- The external drive is not mounted at `DEST`
- Docker daemon is not reachable

### 2 — PostgreSQL dump

```bash
docker exec docmost-postgresql \
  pg_dumpall -U "${POSTGRES_USER:-docmost}" \
  | gzip > postgres_dump.sql.gz
```

Runs `pg_dumpall` inside the running `docmost-postgresql` container and pipes the output directly to a compressed file. Reads `POSTGRES_USER` from `.env` (defaults to `docmost` if the file is absent).

### 3 — Docker volume snapshots

For each named volume, a temporary Alpine container mounts the volume read-only and tars its contents into the backup directory:

```bash
docker run --rm \
  -v "${volume}:/data:ro" \
  -v "${BACKUP_DIR}:/backup" \
  alpine \
  tar czf "/backup/${volume}.tar.gz" -C /data .
```

The running service containers are **not stopped** — this is a live backup. For Docmost/PostgreSQL the SQL dump (step 2) is the authoritative restore path; the volume tar is a convenience copy.

### 4 — Repo config files

Key config files are copied flat into `repo/` inside the backup directory. This includes the production `.env` (which contains secrets — see [Security](#security) below).

### 5 — Rotation

```bash
ls -dt "${DEST}"/ai-server_* | tail -n +$((KEEP + 1)) | xargs -r rm -rf
```

Keeps the **7 most recent** dated backup directories. Anything older is deleted automatically. Change `KEEP=7` at the top of the script to adjust retention.

---

## Backup directory layout

Each run creates a timestamped directory:

```
/media/hectorsvillai/VXSLAUTH/Backups/
└── ai-server_2026-03-20_18-56-23/
    ├── postgres_dump.sql.gz
    ├── ai-server_open_webui_data.tar.gz
    ├── ai-server_docmost_data.tar.gz
    ├── ai-server_caddy_data.tar.gz
    ├── ai-server_caddy_config.tar.gz
    ├── ai-server_redis_data.tar.gz
    └── repo/
        ├── .env
        ├── Caddyfile
        ├── docker-compose.yml
        ├── config/
        ├── assets/
        └── tools/
```

---

## Running the backup

```bash
# From the project root
bash tools/backup.sh
```

Requires: external drive mounted, Docker running, all service containers up.
Typical runtime: 1–3 minutes. Typical size: ~1 GB (dominated by Open WebUI data).

### Automate with cron

```bash
crontab -e
```

```cron
# Daily at 02:00
0 2 * * * /bin/bash /home/hectorsvillai/Desktop/ai-server/tools/backup.sh >> /var/log/ai-server-backup.log 2>&1
```

The preflight check will silently exit with an error (logged) on nights the drive is not connected.

---

## Restore procedure

### PostgreSQL (Docmost database)

```bash
# Drop and recreate the database, then restore
docker exec -i docmost-postgresql psql -U docmost -c "DROP DATABASE docmost; CREATE DATABASE docmost;"
zcat postgres_dump.sql.gz | docker exec -i docmost-postgresql psql -U docmost
```

### Docker volume

```bash
# Example: restore open_webui_data
docker run --rm \
  -v ai-server_open_webui_data:/data \
  -v /path/to/backup:/backup:ro \
  alpine \
  tar xzf /backup/ai-server_open_webui_data.tar.gz -C /data
```

Repeat for each volume you need to restore, then `docker compose up -d`.

### TLS certificates (caddy_data)

Restoring `ai-server_caddy_data.tar.gz` recovers your existing certificates and avoids hitting Let's Encrypt rate limits after a fresh deploy.

---

## Security

The `repo/` section of every backup contains a copy of `.env` (secrets, tokens, passwords). Treat the backup drive with the same care as the `.env` file itself:
- Keep the drive encrypted if possible.
- Do not push backup directories to cloud storage unencrypted.
- The drive is already excluded from the git repo via `.gitignore`.
