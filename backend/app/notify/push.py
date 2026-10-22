"""Web Push в браузере: подписки, шифрование по RFC 8291 (aes128gcm) и подпись VAPID (RFC 8292).

Без сторонних push-библиотек: только `cryptography` и `httpx`. Ключ VAPID задаётся переменной VAPID_PRIVATE_KEY
(32 байта в base64url) или генерируется при первом запуске и хранится в БД (таблица meta) — подписки браузеров
привязаны к публичному ключу, поэтому он не должен меняться между перезапусками.

Когда отправляем:
- новый риск высокой важности в топе ленты подписанной компании — сразу после сбора новостей (не чаще одного
  уведомления за проход и не повторяя событие);
- раз в неделю — дайджест (понедельник, 9:00 МСК);
- по кнопке «Отправить тестовое уведомление».
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.storage import db

log = logging.getLogger(__name__)
SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:radar@alfa-hack.example")
RECORD_SIZE = 4096


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def ub64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _private_key() -> ec.EllipticCurvePrivateKey:
    raw = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not raw:
        row = db.query_one("SELECT value FROM meta WHERE key='vapid_private'")
        if row:
            raw = row["value"]
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            raw = b64u(key.private_numbers().private_value.to_bytes(32, "big"))
            with db.tx() as conn:
                conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('vapid_private', ?)", (raw,))
            raw = db.query_one("SELECT value FROM meta WHERE key='vapid_private'")["value"]  # гонка двух воркеров
    return ec.derive_private_key(int.from_bytes(ub64u(raw), "big"), ec.SECP256R1())


def _raw_public(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key() -> str:
    """applicationServerKey для pushManager.subscribe (base64url, 65 байт несжатой точки P-256)."""
    return b64u(_raw_public(_private_key()))


def vapid_header(endpoint: str) -> str:
    u = urlparse(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SUBJECT},
                             separators=(",", ":")).encode())
    key = _private_key()
    r, s = decode_dss_signature(key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{sig}, k={public_key()}"


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Тело запроса aes128gcm (RFC 8188 + RFC 8291): заголовок (salt, rs, keyid) и одна запись."""
    ua_pub_raw = ub64u(p256dh)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_raw)
    as_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_pub_raw = _raw_public(as_key)
    shared = as_key.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(ub64u(auth), shared, b"WebPush: info\x00" + ua_pub_raw + as_pub_raw, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 — признак последней записи
    return salt + struct.pack(">I", RECORD_SIZE) + bytes([len(as_pub_raw)]) + as_pub_raw + body


def send(sub: dict, message: dict, urgency: str = "normal") -> int:
    """Отправить одно уведомление. Возвращает HTTP-статус push-сервиса (201 — принято)."""
    keys = sub["keys"] if isinstance(sub["keys"], dict) else json.loads(sub["keys"])
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), keys["p256dh"], keys["auth"])
    headers = {"Authorization": vapid_header(sub["endpoint"]), "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream", "TTL": "86400", "Urgency": urgency}
    try:
        r = httpx.post(sub["endpoint"], content=body, headers=headers, timeout=10)
        status = r.status_code
    except httpx.HTTPError as e:
        log.info("Push не отправлен: %s", e)
        status = 0
    with db.tx() as conn:
        if status in (404, 410):  # подписка отозвана браузером — удаляем
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (sub["endpoint"],))
        elif 200 <= status < 300:
            conn.execute("UPDATE push_subscriptions SET last_ok=CURRENT_TIMESTAMP, fails=0 WHERE endpoint=?", (sub["endpoint"],))
        else:
            conn.execute("UPDATE push_subscriptions SET fails=fails+1 WHERE endpoint=?", (sub["endpoint"],))
    return status


# ------------------------------------------------------------------ подписки
def subscribe(user: str, company_uid: str, role: str | None, subscription: dict) -> None:
    ep = subscription.get("endpoint", "")
    keys = subscription.get("keys") or {}
    if not ep.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("Некорректная подписка")
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO push_subscriptions(endpoint, user_id, company_uid, role, keys) VALUES (?,?,?,?,?)",
                     (ep, user, company_uid, role, json.dumps({"p256dh": keys["p256dh"], "auth": keys["auth"]})))


def unsubscribe(user: str, endpoint: str) -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?", (endpoint, user))


def subscriptions(user: str | None = None) -> list[dict]:
    sql = "SELECT * FROM push_subscriptions" + (" WHERE user_id=?" if user else "")
    return [dict(r) for r in db.query(sql, (user,) if user else ())]


def _mark(endpoint: str, event_id: str, kind: str) -> bool:
    """Отмечает отправку; False — такое уведомление уже уходило."""
    with db.tx() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO push_sent(endpoint, event_id, kind) VALUES (?,?,?)", (endpoint, event_id, kind))
        return cur.rowcount > 0


# ------------------------------------------------------------------ что отправлять
def risk_candidates(company_uid: str, role: str | None, days: int = 2) -> list:
    """Новые риски высокой важности в топ-5 ленты компании (за последние `days` дней)."""
    from app.data.profiles import get_profile
    from app.news.ingest import list_events
    from app.ranking.scorer import LIVE_TRUST, rank_feed

    p = get_profile(company_uid)
    if not p:
        return []
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()[:10]
    top = rank_feed(list_events(300), p, role, limit=5)
    return [s for s in top if s.effect in ("risk", "mixed") and s.event.classification.importance >= 2
            and (s.event.published_at or "")[:10] >= since and s.event.classification.trust in LIVE_TRUST]


def notify_new_risks() -> int:
    """После сбора новостей: по одному уведомлению о самом важном новом риске на подписку."""
    sent = 0
    for sub in subscriptions():
        for s in risk_candidates(sub["company_uid"], sub["role"]):
            if not _mark(sub["endpoint"], s.event.id, "risk"):
                continue
            why = s.reasons[0]["why"] if s.reasons else ""
            if 200 <= send(sub, {"title": "Новый риск для вашей компании", "body": f"{s.event.title}. {why}"[:240],
                                 "url": f"/?event={s.event.id}", "tag": f"risk-{s.event.id}"}, urgency="high") < 300:
                sent += 1
            break
    return sent


def notify_weekly_digest() -> int:
    from app.data.profiles import get_profile
    from app.news.ingest import list_events
    from app.ranking.scorer import rank_feed

    week = datetime.now(UTC).strftime("%G-W%V")
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()[:10]
    sent = 0
    for sub in subscriptions():
        if not _mark(sub["endpoint"], week, "digest"):
            continue
        p = get_profile(sub["company_uid"])
        if not p:
            continue
        top = [s for s in rank_feed(list_events(300), p, sub["role"], limit=10) if (s.event.published_at or "")[:10] >= since]
        risks = sum(s.effect == "risk" for s in top)
        opps = sum(s.effect == "opportunity" for s in top)
        body = (f"За неделю: {risks} риск(а), {opps} возможност(и). Главное: {top[0].event.title}" if top
                else "За неделю значимых для компании событий не было — это тоже ответ.")
        if 200 <= send(sub, {"title": "Дайджест недели · Бизнес-радар", "body": body[:240], "url": "/?view=digest",
                             "tag": f"digest-{week}"}) < 300:
            sent += 1
    return sent
