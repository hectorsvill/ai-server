#!/usr/bin/env bash
# update_containers.sh — Pull new Docker images and recreate changed containers.
# Designed to run at 2:30 AM daily, before the 3:00 AM backup and nightly reboot.
#
# Cron entry (root):
#   30 2 * * * /bin/bash /home/hectorsvillai/Desktop/ai-server/tools/update_containers.sh >> /var/log/ai-server-update.log 2>&1

set -euo pipefail

COMPOSE_DIR="/home/hectorsvillai/Desktop/ai-server"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')] update_containers:"

log() { echo "$LOG_PREFIX $*"; }

cd "$COMPOSE_DIR"

log "Starting container update check"

# ── Pull latest images ─────────────────────────────────────────────────────────
# Capture output to detect which images actually changed
PULL_OUTPUT=$(docker compose pull --quiet 2>&1 || true)

# docker compose pull prints "Pulled" for each image that changed
UPDATED=$(echo "$PULL_OUTPUT" | grep -c "Pulled" 2>/dev/null || true)

if [ "$UPDATED" -eq 0 ]; then
    log "All images up to date — no restart needed"
    exit 0
fi

log "$UPDATED image(s) updated — recreating containers"
echo "$PULL_OUTPUT" | grep "Pulled" | while read -r line; do
    log "  $line"
done

# ── Rebuild Caddy (custom image — must build, not just pull) ───────────────────
if echo "$PULL_OUTPUT" | grep -q "caddy\|Caddy" 2>/dev/null; then
    log "Rebuilding Caddy (custom DNS plugin image)"
    docker compose build --quiet caddy
fi

# ── Recreate containers with new images ───────────────────────────────────────
# --no-build: don't rebuild unless we did it above
# --remove-orphans: clean up stale containers
docker compose up -d --remove-orphans

log "Containers recreated. Waiting 15s for health checks..."
sleep 15

# ── Verify all expected containers are running ────────────────────────────────
EXPECTED=(caddy open-webui docmost docmost_db redis n8n glance)
ALL_OK=true

for svc in "${EXPECTED[@]}"; do
    STATUS=$(docker compose ps --format "{{.Name}} {{.Status}}" 2>/dev/null | grep "^${COMPOSE_DIR##*/}-${svc}" | head -1 || true)
    if echo "$STATUS" | grep -q "Up"; then
        log "  OK  $svc"
    else
        log "  WARN  $svc not running after update"
        ALL_OK=false
    fi
done

# ── Prune old images (keep disk clean) ────────────────────────────────────────
log "Pruning dangling images"
docker image prune -f --filter "until=48h" >> /dev/null 2>&1 || true

if $ALL_OK; then
    log "Update complete — all containers healthy"
else
    log "Update complete — WARNING: one or more containers not healthy, check logs"
    exit 1
fi
