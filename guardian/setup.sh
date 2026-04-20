#!/usr/bin/env bash
# ============================================================
# AI Guardian — Setup Script
# Run as root from the guardian/ directory:
#   sudo bash setup.sh
# ============================================================
set -euo pipefail

GUARDIAN_DIR="/home/hectorsvillai/Desktop/ai-server/guardian"
VENV_DIR="$GUARDIAN_DIR/venv"
USER_HOME="/home/hectorsvillai"

echo "===> AI Guardian Setup"
echo "     Directory: $GUARDIAN_DIR"
echo ""

# ── 1. Check Python ───────────────────────────────────────────
echo "[1/6] Checking Python..."
PYTHON=$(which python3.11 2>/dev/null || which python3.12 2>/dev/null || which python3 2>/dev/null || true)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ required but not found"
    exit 1
fi
echo "     Using: $PYTHON ($($PYTHON --version))"

# ── 2. Create virtualenv ──────────────────────────────────────
echo "[2/6] Creating virtualenv at $VENV_DIR..."
$PYTHON -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# ── 3. Install dependencies ───────────────────────────────────
echo "[3/6] Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$GUARDIAN_DIR/requirements.txt"
echo "     Done."

# ── 4. Create data/log directories ───────────────────────────
echo "[4/6] Creating data and log directories..."
mkdir -p "$GUARDIAN_DIR/data"
mkdir -p "$GUARDIAN_DIR/logs"
chmod 750 "$GUARDIAN_DIR/data" "$GUARDIAN_DIR/logs"

# ── 5. Install systemd service ────────────────────────────────
echo "[5/6] Installing systemd service..."
cp "$GUARDIAN_DIR/ai-guardian.service" /etc/systemd/system/ai-guardian.service
systemctl daemon-reload
echo "     Service installed: ai-guardian.service"
echo "     To enable on boot: sudo systemctl enable ai-guardian"
echo "     To start now:      sudo systemctl start ai-guardian"

# ── 6. Test configuration ─────────────────────────────────────
echo "[6/6] Testing configuration..."
cd "$GUARDIAN_DIR"
PYTHONPATH="/home/hectorsvillai/Desktop/ai-server" \
    "$VENV_DIR/bin/python" -c "
from guardian.core.config import cfg
print(f'     Domain:      {cfg.server.domain}')
print(f'     Services:    {[s.name for s in cfg.docker.services]}')
print(f'     AI model:    {cfg.ai.model}')
print(f'     API port:    {cfg.service.port}')
print(f'     Dry-run:     {cfg.safety.dry_run}')
print('     Config OK.')
"

echo ""
echo "===> Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Review config.yaml and adjust thresholds/notifications"
echo "  2. Set notification env vars in ai-guardian.service (optional)"
echo "  3. sudo systemctl enable --now ai-guardian"
echo "  4. Check status: sudo systemctl status ai-guardian"
echo "  5. API dashboard: http://127.0.0.1:9900/docs"
echo "  6. Tail logs: tail -f $GUARDIAN_DIR/logs/guardian.log"
