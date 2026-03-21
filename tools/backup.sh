#!/usr/bin/env bash
# backup.sh — backs up ai-server data to an external drive
# Backs up: PostgreSQL dump, Docker volumes, repo config files
# Skips: ollama_data (models are re-downloadable and can be tens of GB)
# Retains the last 7 dated backups (older ones are pruned automatically)

set -euo pipefail

source "$(dirname "$0")/../.env" 2>/dev/null || true
DEST="${BACKUP_DEST:?BACKUP_DEST is not set in .env}"
PROJECT_DIR="/home/hectorsvillai/Desktop/ai-server"
DATE=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_DIR="${DEST}/ai-server_${DATE}"
KEEP=7

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────

if [ ! -d "${DEST}" ]; then
  die "Backup destination ${DEST} is not mounted or does not exist."
fi

if ! docker info &>/dev/null; then
  die "Docker is not running or current user cannot reach it."
fi

mkdir -p "${BACKUP_DIR}"
log "Backup started → ${BACKUP_DIR}"

# ── PostgreSQL dump ──────────────────────────────────────────────────────────

log "Dumping PostgreSQL (docmost_db)..."
docker exec docmost-postgresql \
  pg_dumpall -U "${POSTGRES_USER:-docmost}" \
  | gzip > "${BACKUP_DIR}/postgres_dump.sql.gz"

log "  postgres_dump.sql.gz done"

# ── Docker volumes ───────────────────────────────────────────────────────────

backup_volume() {
  local volume="$1"
  local filename="${volume}.tar.gz"
  log "  Backing up volume: ${volume}..."
  docker run --rm \
    -v "${volume}:/data:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine \
    tar czf "/backup/${filename}" -C /data .
  log "  ${filename} done"
}

log "Backing up Docker volumes..."
backup_volume "ai-server_open_webui_data"
backup_volume "ai-server_docmost_data"
backup_volume "ai-server_caddy_data"
backup_volume "ai-server_caddy_config"
backup_volume "ai-server_redis_data"

# ── Repo config files ────────────────────────────────────────────────────────

log "Backing up repo config files..."
mkdir -p "${BACKUP_DIR}/repo"

cp "${PROJECT_DIR}/Caddyfile"            "${BACKUP_DIR}/repo/"
cp "${PROJECT_DIR}/.env"                 "${BACKUP_DIR}/repo/"
cp "${PROJECT_DIR}/docker-compose.yml"   "${BACKUP_DIR}/repo/"
cp -r "${PROJECT_DIR}/config"            "${BACKUP_DIR}/repo/config"
cp -r "${PROJECT_DIR}/assets"            "${BACKUP_DIR}/repo/assets"
cp -r "${PROJECT_DIR}/tools"             "${BACKUP_DIR}/repo/tools"

log "  repo files done"

# ── Rotation: prune old backups ──────────────────────────────────────────────

log "Pruning old backups (keeping last ${KEEP})..."
ls -dt "${DEST}"/ai-server_* 2>/dev/null \
  | tail -n +$((KEEP + 1)) \
  | xargs -r rm -rf

# ── Done ─────────────────────────────────────────────────────────────────────

SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
log "Backup complete. Size: ${SIZE}"
log "Location: ${BACKUP_DIR}"
