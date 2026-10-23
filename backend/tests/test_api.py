"""Проверка API: RBAC, лента, инсайт, чат, анализ новой новости, метрики."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def demo(client):
    users = client.get("/api/demo-users").json()["users"]
    return users[0]


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_rbac(client, demo):
    assert client.get(f"/api/feed?company_id={demo['primary_company']}").status_code == 401
    assert client.get(f"/api/feed?company_id={demo['primary_company']}", headers={"X-User-Id": "F_NOBODY"}).status_code == 403


def test_feed_insight_chat(client, demo):
    h = {"X-User-Id": demo["user_id"]}
    feed = client.get(f"/api/feed?company_id={demo['primary_company']}&role={demo['role']}", headers=h).json()
    assert feed["items"], "лента не должна быть пустой"
    ev = feed["items"][0]["event_id"]
    ins = client.get(f"/api/insights/{ev}?company_id={demo['primary_company']}&role={demo['role']}", headers=h).json()
    assert ins["takeaway"]["text"] and ins["sources"]
    trace = client.get(f"/api/trace/{ins['trace_id']}", headers=h).json()
    assert len(trace["steps"]) >= 6
    r = client.post("/api/chat", headers=h, json={"event_id": ev, "company_id": demo["primary_company"], "role": demo["role"],
                                                  "question": "Откуда эти выводы?"}).json()
    assert "Источники" in r["answer"]
    assert client.post("/api/feedback", headers=h, json={"event_id": ev, "company_id": demo["primary_company"], "rating": 1}).json()["ok"]


def test_analyze_unseen_text(client, demo):
    h = {"X-User-Id": demo["user_id"]}
    r = client.post("/api/news/analyze", headers=h, json={
        "text": "Минфин предложил повысить НДС до 22% с 2027 года. Мера должна пополнить бюджет на фоне растущих расходов.",
        "company_id": demo["primary_company"], "role": "accountant"}).json()
    factors = {f["factor"] for f in r["classification"]["factors"]}
    assert "tax_regulation" in factors
    assert r["insight"]["what_happened"]


def test_metrics(client):
    m = client.get("/api/metrics").json()
    assert m["llm"]["mode"] == "mock" and m["insights"]["total"] >= 1


def test_push_subscribe_and_encrypt(client, demo):
    """Подписка на push: ключ VAPID, сохранение подписки, шифрование по RFC 8291 расшифровывается «браузером»."""
    import os
    import struct

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app.notify import push

    key = client.get("/api/push/key").json()["public_key"]
    assert len(push.ub64u(key)) == 65 and key == push.public_key()  # ключ стабилен между вызовами
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    ua_raw = push._raw_public(ua)
    sub = {"endpoint": "https://push.example.test/abc", "keys": {"p256dh": push.b64u(ua_raw), "auth": push.b64u(auth)}}
    headers = {"X-User-Id": demo["user_id"]}
    company = demo["primary_company"]
    assert client.post("/api/push/subscribe", headers=headers, json={"company_id": company, "subscription": sub}).status_code == 200
    assert client.post("/api/push/subscribe", headers=headers,
                       json={"company_id": company, "subscription": {"endpoint": "http://x", "keys": {}}}).status_code == 400
    body = push.encrypt(b'{"title":"t"}', sub["keys"]["p256dh"], sub["keys"]["auth"])
    salt, idlen = body[:16], body[20]
    assert struct.unpack(">I", body[16:20])[0] == 4096
    as_raw, ct = body[21:21 + idlen], body[21 + idlen:]
    shared = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_raw))
    ikm = push._hkdf(auth, shared, b"WebPush: info\x00" + ua_raw + as_raw, 32)
    cek = push._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = push._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    assert AESGCM(cek).decrypt(nonce, ct, None) == b'{"title":"t"}\x02'
    assert push.vapid_header(sub["endpoint"]).startswith("vapid t=")
    assert client.post("/api/push/unsubscribe", headers=headers, json={"endpoint": sub["endpoint"]}).status_code == 200


def test_watch_api_and_metrics_live_share(client, demo):
    h = {"X-User-Id": demo["user_id"]}
    c = demo["primary_company"]
    opts = client.get(f"/api/watch/options?company_id={c}", headers=h).json()
    assert opts["topic"] and "region" in opts
    assert client.post(f"/api/companies/{c}/watch", headers=h, json={"kind": "topic", "value": "nope"}).status_code == 400
    r = client.post(f"/api/companies/{c}/watch", headers=h, json={"kind": "topic", "value": "fx_rub"}).json()
    assert r["watch"][0]["title"] == "Курс рубля"
    assert client.get(f"/api/companies/{c}", headers=h).json()["watch"]
    assert client.delete(f"/api/companies/{c}/watch?kind=topic&value=fx_rub", headers=h).json()["watch"] == []
    ls = client.get("/api/metrics").json()["live_share"]
    assert ls["target"] == 0.7 and "by_persona" in ls


def test_importance_level_not_inflated():
    from app.main import importance_level

    assert importance_level(1.2, None)["level"] == 3 and importance_level(0.7, None)["level"] == 2
    assert importance_level(0.4, None)["level"] == 1
    # ИИ после разбора может понизить важность, а поднять — не больше чем на ступень выше профиля
    assert importance_level(1.2, (1, "слабо"))["level"] == 1
    assert importance_level(0.4, (3, "существенно"))["level"] == 2
    assert importance_level(0.4, (3, ""))["source"] == "ai"


def test_money_at_stake_no_double_count(client, demo):
    h = {"X-User-Id": demo["user_id"]}
    m = client.get(f"/api/money?company_id={demo['primary_company']}&role={demo['role']}", headers=h).json()
    assert {"up", "down", "protect", "levers"} <= set(m)
    keys = [v["lever"] for v in m["levers"] if v["lever"] in ("rate", "fx", "wages")]
    assert len(keys) == len(set(keys))  # одна причина — одна строка, а не по строке на новость
    assert m["down"] <= m["protect"] <= 0 or m["protect"] >= m["down"]
    assert m["up"] == sum(v["up"] for v in m["levers"]) and m["down"] == sum(v["down"] for v in m["levers"])



def test_tasks_lifecycle(client, demo):
    """«Мои действия»: шаг из разбора взят в работу, отмечен выполненным, удалён; дубль не создаётся."""
    h, cid = {"X-User-Id": demo["user_id"]}, demo["primary_company"]
    body = {"company_id": cid, "text": "Пересчитать график процентных платежей", "owner": "финдиректор", "due": "до 30.11.2026"}
    r = client.post("/api/tasks", headers=h, json=body).json()
    assert r["ok"] and not r.get("duplicate")
    assert client.post("/api/tasks", headers=h, json=body).json()["duplicate"]
    tasks = client.get(f"/api/tasks?company_id={cid}", headers=h).json()["tasks"]
    t = next(x for x in tasks if x["id"] == r["id"])
    assert t["status"] == "open" and t["owner"] == "финдиректор" and t["due"] == "до 30.11.2026"
    assert client.patch(f"/api/tasks/{t['id']}", headers=h, json={"company_id": cid, "status": "done"}).status_code == 200
    assert next(x for x in client.get(f"/api/tasks?company_id={cid}", headers=h).json()["tasks"] if x["id"] == t["id"])["status"] == "done"
    assert client.delete(f"/api/tasks/{t['id']}?company_id={cid}", headers=h).status_code == 200
    assert client.delete(f"/api/tasks/{t['id']}?company_id={cid}", headers=h).status_code == 404
