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
    assert "sensitivity" in m and "candidates" in m  # чувствительность есть всегда — карточка не бывает пустой


def test_money_ai_filters_irrelevant_events(client, demo, monkeypatch):
    """ИИ отбирает события для «денег на кону»: отсеянное моделью не попадает в рычаги, направление и причина — от модели,
    суммы — по-прежнему калькулятор."""
    import app.agent.money as money

    seen = {}

    def fake(p, role, cands):
        seen["n"] = len(cands)
        keep = cands[0]["key"] if cands else None
        return ({c["key"]: {"key": c["key"], "n": i + 1, "relevant": c["key"] == keep, "direction": "worse", "strength": 3,
                            "why": "Проверка: событие дорожит кредит компании"} for i, c in enumerate(cands)},
                {"used": True, "model": "test"})

    monkeypatch.setattr(money, "ai_check", fake)
    h = {"X-User-Id": demo["user_id"]}
    m = client.get(f"/api/money?company_id={demo['primary_company']}&role={demo['role']}", headers=h).json()
    assert m["ai"]["used"] and m["candidates"] <= seen["n"]
    if seen["n"]:
        assert m["events_used"] == 1 and m["dropped"] == m["candidates"] - 1
        ev = m["levers"][0]["events"][0]
        assert ev["direction"] == "worse" and ev["why"].startswith("Проверка") and m["levers"][0]["tilt"] == "worse"



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
def test_calendar_within_horizon(client, demo):
    """Календарь: заседания ЦБ из config/calendar.yaml только в горизонте, с прогнозом банка; чужая компания — 403."""
    h, cid = {"X-User-Id": demo["user_id"]}, demo["primary_company"]
    c = client.get(f"/api/calendar?company_id={cid}&days=365", headers=h).json()
    assert c["horizon_days"] == 365 and c["source_url"].startswith("https://www.cbr.ru")
    assert c["items"] and all(c["today"] <= x["date"] <= c["until"] and x["kind"] == "cbr" for x in c["items"])
    assert client.get("/api/calendar?company_id=U_NOBODY", headers=h).status_code in (403, 404)


def test_proof_summary(client):
    """«Как мы проверяли»: цифры из отчётов в репозитории, без входа (это открытые данные проекта)."""
    p = client.get("/api/proof").json()
    assert p["offline"]["p3"] and p["repo"].startswith("https://github.com/")
    assert p["redteam"]["total"] >= p["redteam"]["passed"] >= 0


def test_money_bank_revisions_are_facts_with_source(client, demo):
    """«Уже изменилось»: разница двух опубликованных прогнозов банка, сумма — калькулятор, у строки есть ссылка на прогноз."""
    from datetime import date

    from app.agent.money import bank_revisions, horizon_year
    from app.data.profiles import get_profile

    assert horizon_year(date(2026, 10, 24)) == 2027 and horizon_year(date(2026, 3, 1)) == 2026
    rows = bank_revisions(get_profile(demo["primary_company"]))
    for r in rows:
        assert r["ref"].startswith("bank:fc-") and r["from"] != r["to"] and abs(r["effect_rub"]) >= 1000
    h = {"X-User-Id": demo["user_id"]}
    m = client.get(f"/api/money?company_id={demo['primary_company']}&role={demo['role']}", headers=h).json()
    assert "revisions" in m


def test_money_with_my_numbers_and_numeric_formulas(client, demo):
    """Этап 2: «мои цифры» пересчитывают деньги на кону; у строк калькулятора — формула с подставленными числами;
    запас прочности — тем же калькулятором."""
    h, cid = {"X-User-Id": demo["user_id"]}, demo["primary_company"]
    base = client.get(f"/api/money?company_id={cid}&role={demo['role']}", headers=h).json()
    assert "buffers" in base and not base["user_numbers"]
    mine = client.post("/api/money", headers=h, json={"company_id": cid, "role": demo["role"],
                                                      "numbers": {"debt_rub": 0, "deposits_rub": 0, "revenue_rub": 1e9}}).json()
    assert mine["user_numbers"]
    assert all(b["lever"] != "rate" for b in mine["buffers"])  # без долга ставка не съест выручку
    bad = client.post("/api/money", headers=h, json={"company_id": cid, "numbers": {"floating_share": 5}})
    assert bad.status_code == 422
    r = client.post("/api/scenario", headers=h, json={"company_id": cid, "inputs": {"debt_rub": 100e6, "floating_share": 0.5, "deposits_rub": 0,
                                                                                   "rate_delta_pp": 1.0}}).json()
    rate = next(x for x in r["result"]["rows"] if x["lever"] == "rate")
    assert rate["calc"].startswith("−100,0 млн ₽ × 50% × 1,0 п.п.") and rate["effect_rub"] == -500_000
    assert any(b["lever"] == "rate" and b["value"] > 0 for b in r["buffers"]) or not r["defaults"].get("revenue_rub")


def test_feed_filtered_explains_what_was_not_shown(client, demo):
    """Этап 3: «что сервис не показал и почему» — группы причин с примерами; события ленты туда не попадают."""
    h, cid = {"X-User-Id": demo["user_id"]}, demo["primary_company"]
    feed = client.get(f"/api/feed?company_id={cid}&limit=40", headers=h).json()["items"]
    r = client.get(f"/api/feed/filtered?company_id={cid}", headers=h).json()
    assert {"pool", "shown", "noise", "groups"} <= set(r)
    shown = {x["event_id"] for x in feed}
    for g in r["groups"]:
        assert g["n"] >= len(g["examples"]) and g["title"]
        assert not ({e["event_id"] for e in g["examples"]} & shown)
    assert len(feed[0]["reasons"]) <= 5


def test_bank_library_documents_search_and_company_theses(client, demo):
    """Этап 4: материалы банка как библиотека — документ целиком, поиск по тезисам, тезисы под компанию, ставки рынка."""
    h, cid = {"X-User-Id": demo["user_id"]}, demo["primary_company"]
    lib = client.get("/api/bank/documents").json()
    assert lib["stats"]["theses"] > 0 and lib["documents"]
    doc = client.get(f"/api/bank/documents/{lib['documents'][0]['doc_id']}").json()
    assert doc["theses"] and all(t["ref"].startswith("bank:") for t in doc["theses"])
    assert client.get("/api/bank/documents/nope").status_code == 404
    found = client.get("/api/bank/search?q=ключевая ставка", headers=h).json()["results"]
    assert found and found[0]["doc_title"] and found[0]["ref"].startswith("bank:")
    assert client.get("/api/bank/search?q=ставка").status_code == 401  # поиск — для вошедших
    mine = client.get(f"/api/bank/for-company?company_id={cid}", headers=h).json()["theses"]
    assert len(mine) <= 3 and all(t["doc_title"] for t in mine)
    rm = client.get("/api/bank/rate-market").json()
    assert rm["bank"] and all(x["value"] > 0 for x in rm["bank"] + rm["market"])


def test_me_overview_counts_per_company(client, demo):
    """Этап 5: переключатель «мои компании» — у каждой доступной компании счётчик рисков и свежих событий."""
    h = {"X-User-Id": demo["user_id"]}
    me = client.get("/api/me", headers=h).json()
    ov = client.get("/api/me/overview", headers=h).json()["companies"]
    assert {c["uid"] for c in ov} == {c["uid"] for c in me["companies"]}
    for c in ov:
        assert c["risks"] >= 0 and c["fresh"] >= 0


def test_chat_offtopic_money_and_feed(client, demo):
    """Этап 6: честный отказ не по теме, суммы — из калькулятора, чат по всей ленте со ссылками на события."""
    h, cid = {"X-User-Id": demo["user_id"]}, demo["primary_company"]
    ev = client.get(f"/api/feed?company_id={cid}", headers=h).json()["items"][0]["event_id"]
    body = {"event_id": ev, "company_id": cid, "role": demo["role"]}
    r = client.post("/api/chat", headers=h, json={**body, "question": "Напиши стих про осень"}).json()
    assert r["mode"] == "guard" and "не могу" in r["answer"]
    r = client.post("/api/chat", headers=h, json={**body, "question": "Сколько это стоит нам в рублях?", "style": "owner"}).json()
    assert r["answer"]
    s = client.post("/api/chat/feed", headers=h, json={"company_id": cid, "role": demo["role"], "question": "Что главное на неделе?"})
    assert s.status_code == 200 and '"events"' in s.text and "[ev:" in s.text


def test_task_kanban_and_request_statuses(client, demo):
    """Этап 7: «в работе» и смена исполнителя у задачи; обращения в банк со статусом, клиент отмечает «решено»."""
    h, cid = {"X-User-Id": demo["user_id"]}, demo["primary_company"]
    t = client.post("/api/tasks", headers=h, json={"company_id": cid, "text": "Проверить ковенанты по кредиту"}).json()
    assert client.patch(f"/api/tasks/{t['id']}", headers=h, json={"company_id": cid, "status": "doing", "owner": "Казначей"}).status_code == 200
    row = next(x for x in client.get(f"/api/tasks?company_id={cid}", headers=h).json()["tasks"] if x["id"] == t["id"])
    assert row["status"] == "doing" and row["owner"] == "Казначей"
    assert client.patch(f"/api/tasks/{t['id']}", headers=h, json={"company_id": cid}).status_code == 400
    a = client.post("/api/actions", headers=h, json={"company_id": cid, "kind": "application", "product": "Кредит", "comment": "Сумма 5 млн"}).json()
    items = client.get(f"/api/actions?company_id={cid}", headers=h).json()["items"]
    me = next(x for x in items if x["ticket"] == a["ticket"])
    assert me["status"] == "accepted" and me["product"] == "Кредит"
    assert client.patch(f"/api/actions/{me['id']}", headers=h, json={"company_id": cid, "status": "resolved"}).status_code == 200
    assert next(x for x in client.get(f"/api/actions?company_id={cid}", headers=h).json()["items"] if x["id"] == me["id"])["status"] == "resolved"
