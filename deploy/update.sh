#!/bin/bash
# OxySync auto-updater — run via cron every minute

BOT_DIR="/home/YOUR_USER/oxysync"
SERVICE="oxysync"
LOG="$BOT_DIR/deploy/update.log"
VENV="$BOT_DIR/venv/bin"

cd "$BOT_DIR" || exit 1

git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Новый коммит: $REMOTE" >> "$LOG"

REQS_BEFORE=$(git show HEAD:requirements.txt 2>/dev/null)
git pull origin main --quiet

REQS_AFTER=$(cat requirements.txt)
if [ "$REQS_BEFORE" != "$REQS_AFTER" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] requirements.txt изменён, обновляю зависимости..." >> "$LOG"
    "$VENV/pip" install -r requirements.txt --quiet
fi

systemctl restart "$SERVICE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Перезапущен успешно" >> "$LOG"
