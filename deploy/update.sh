#!/usr/bin/env bash
# Обновление до последней версии main: git pull + пересборка контейнера. БД и кэш LLM в data/processed сохраняются.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
for i in $(seq 1 60); do curl -sf http://127.0.0.1:8000/api/health && echo && exit 0; sleep 3; done
echo "Сервис не ответил — смотрите логи: docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs app"
exit 1
