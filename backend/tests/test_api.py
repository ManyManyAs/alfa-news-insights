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
