#!/usr/bin/env bash
# Установка «Бизнес-радара» на чистый Ubuntu 22.04/24.04 (запускать от root из каталога репозитория):
#
#   git clone https://github.com/ManyManyAs/alfa-news-insights.git /opt/alfa-news-insights
#   cd /opt/alfa-news-insights && bash deploy/install.sh
#
# Или автоматически из GitHub Actions: workflow «Deploy» (.github/workflows/deploy.yml) копирует код
# на сервер, пишет .env из секретов репозитория и запускает этот же скрипт без вопросов.
#
# Что делает: Docker + Compose, Nginx (reverse proxy, SSE, лимиты запросов к LLM-эндпоинтам), firewall,
# файл .env с ключом (вводится скрытно, права 600), запуск сервиса, опционально HTTPS (если задан DOMAIN).
# Повторный запуск безопасен (идемпотентен). Обновление кода — deploy/update.sh.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${DOMAIN:-}"          # например: DOMAIN=radar.example.ru bash deploy/install.sh
EMAIL="${EMAIL:-}"            # e-mail для Let's Encrypt (нужен вместе с DOMAIN)

log() { printf '\n\033[1;31m▶ %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Запустите от root (sudo bash deploy/install.sh)"; exit 1; }

log "1/6 Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl git nginx ufw

log "2/6 Docker"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

log "3/6 Настройки и ключ к LLM (.env)"
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
fi
if ! grep -q '^LLM_API_KEY=.\+' "$ENV_FILE" && [ -t 0 ]; then
  read -r -s -p "Вставьте LLM_API_KEY (ввод скрыт; Enter — пропустить, сервис будет в режиме replay): " KEY; echo
  if [ -n "$KEY" ]; then
    read -r -p "LLM_BASE_URL (эндпоинт организаторов, …/v1): " URL
    sed -i "s|^LLM_API_KEY=.*|LLM_API_KEY=${KEY}|" "$ENV_FILE"
    sed -i "s|^LLM_BASE_URL=.*|LLM_BASE_URL=${URL}|" "$ENV_FILE"
  fi
fi
grep -q '^ENABLE_SCHEDULER=' "$ENV_FILE" && sed -i 's/^ENABLE_SCHEDULER=.*/ENABLE_SCHEDULER=true/' "$ENV_FILE" || echo "ENABLE_SCHEDULER=true" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Swap для сборки на маленьких VPS (1–2 ГБ RAM)
if [ "$(swapon --show | wc -l)" -eq 0 ] && [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "4/6 Сборка и запуск контейнера"
cd "$APP_DIR"
mkdir -p data/processed
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build --remove-orphans
docker image prune -f >/dev/null || true
echo "Ждём готовности сервиса (первый старт строит профили 85 тыс. компаний, ~30–60 с)…"
for i in $(seq 1 100); do
  curl -sf http://127.0.0.1:8000/api/health >/dev/null && break
  sleep 3
done
curl -sf http://127.0.0.1:8000/api/health && echo || { docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs --tail 80 app; exit 1; }

log "5/6 Nginx"
SERVER_NAME="${DOMAIN:-_}"
sed "s/__SERVER_NAME__/${SERVER_NAME}/" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/alfa-news-insights
ln -sf /etc/nginx/sites-available/alfa-news-insights /etc/nginx/sites-enabled/alfa-news-insights
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "6/6 Firewall и HTTPS"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null
if [ -n "$DOMAIN" ] && [ -n "$EMAIL" ]; then
  apt-get install -y -q certbot python3-certbot-nginx
  certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect
fi

IP="$(curl -s https://api.ipify.org || hostname -I | awk '{print $1}')"
log "Готово: http://${DOMAIN:-$IP}/   (Swagger: /docs)"
echo "Логи:      docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f app"
echo "Обновление: bash deploy/update.sh"
