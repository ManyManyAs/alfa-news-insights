"""Демо-авторизация: вход физлица по логину и паролю, подписанная сессия в HttpOnly-cookie.

В интернет-банке пользователь входит как физлицо и видит компании, где у него есть доступ. В прототипе логин —
идентификатор физлица из данных кейса (F_…), пароль общий для демо-учёток (DEMO_PASSWORD). Сессия — подписанный
HMAC-SHA256 токен без хранения на сервере: {sub, exp}. В продакшене здесь интеграция с IAM банка.

Заголовок X-User-Id принимается только при ALLOW_HEADER_AUTH=true (тесты, локальная отладка).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.config import get_settings
from app.storage import db

COOKIE = "session"
TTL_S = 12 * 3600


def _secret() -> bytes:
    s = get_settings().session_secret
    if s:
        return s.encode()
    row = db.query_one("SELECT value FROM meta WHERE key='session_secret'")
    if row:
        return row["value"].encode()
    value = secrets.token_hex(32)  # генерируется один раз и живёт в БД: перезапуск не разлогинивает
    with db.tx() as conn:
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('session_secret', ?)", (value,))
    return db.query_one("SELECT value FROM meta WHERE key='session_secret'")["value"].encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(user_id: str, ttl_s: int = TTL_S) -> str:
    body = _b64(json.dumps({"sub": user_id, "exp": int(time.time()) + ttl_s}).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        data = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data.get("sub")


def check_password(user_id: str, password: str) -> bool:
    """Пароль демо-учёток общий; войти может только физлицо с доступом хотя бы к одной компании-клиенту."""
    if not hmac.compare_digest(password.encode(), get_settings().demo_password.encode()):
        return False
    return bool(client_companies(user_id))


def client_companies(user_id: str) -> list[str]:
    """Компании физлица, доступные в интернет-банке: только действующие клиенты (CLIENT).

    PROSPECT ещё не имеет доступа к интернет-банку, CLOSE_CLIENT его утратил (ответы организаторов, вопросы 11–12, 57).
    """
    rows = db.query("SELECT ul.uid FROM user_links ul JOIN companies c ON c.uid = ul.uid "
                    "WHERE ul.fid=? AND json_extract(c.profile, '$.client_type') = 'CLIENT'", (user_id,))
    return [r["uid"] for r in rows]


# Ограничитель частоты: простое скользящее окно в памяти процесса (одного воркера достаточно для прототипа)
_hits: dict[str, list[float]] = {}


def rate_limited(key: str, limit: int, window_s: int = 60) -> bool:
    now = time.time()
    q = [t for t in _hits.get(key, []) if now - t < window_s]
    limited = len(q) >= limit
    if not limited:
        q.append(now)
    _hits[key] = q
    return limited
