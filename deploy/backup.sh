#!/usr/bin/env bash
# Резервная копия БД (профили, события, разборы, кэш LLM, обратная связь, журнал доступа).
# Онлайн-копия через sqlite3 backup API внутри контейнера — сервис не останавливается. Храним 7 последних копий.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/processed/backups
STAMP="$(date +%Y%m%d-%H%M%S)"
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml exec -T app python - "$STAMP" <<'PY'
import sqlite3, sys
src = sqlite3.connect("/app/data/processed/app.db")
dst = sqlite3.connect(f"/app/data/processed/backups/app-{sys.argv[1]}.db")
src.backup(dst)
dst.close()
src.close()
PY
gzip -f "data/processed/backups/app-$STAMP.db"
ls -1t data/processed/backups/app-*.db.gz | tail -n +8 | xargs -r rm -f
echo "Резервная копия: data/processed/backups/app-$STAMP.db.gz"
