"""Собирает конфиг Nginx из deploy/nginx.conf: только HTTP или HTTP→HTTPS + сервер на 443.

  python3 deploy/render_nginx.py                       # без домена: приложение по HTTP
  python3 deploy/render_nginx.py 77-1-2-3.sslip.io     # есть сертификат: редирект на HTTPS и сервер на 443
Используется в install.sh и в CI (проверка `nginx -t` на разных версиях Nginx).
"""
import sys
from pathlib import Path

APP = "    include snippets/alfa-radar-app.conf;"


def render(domain: str | None) -> str:
    tpl = (Path(__file__).parent / "nginx.conf").read_text(encoding="utf-8")
    if not domain:
        return tpl.replace("__HTTP_BODY__", APP).replace("__HTTPS__", "")
    https = f"""server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    add_header Strict-Transport-Security "max-age=31536000" always;
{APP}
}}"""
    body = f"    location / {{ return 301 https://{domain}$request_uri; }}"
    return tpl.replace("__HTTP_BODY__", body).replace("__HTTPS__", https)


if __name__ == "__main__":
    print(render(sys.argv[1] if len(sys.argv) > 1 else None))
