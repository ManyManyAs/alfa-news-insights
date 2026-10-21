"""Этап 13: «Коротко» опирается на действия со сроком и исполнителем; важность по оценке модели влияет на ленту."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def demo(client):
    u = client.get("/api/demo-users").json()["users"][0]
    return u, {"X-User-Id": u["user_id"]}


def test_rules_actions_have_owner_and_due(client, demo):
    u, h = demo
    ev = client.get(f"/api/feed?company_id={u['primary_company']}&role={u['role']}", headers=h).json()["items"][0]["event_id"]
    ins = client.get(f"/api/insights/{ev}?company_id={u['primary_company']}&role={u['role']}", headers=h).json()
    assert ins["actions"] and all(a["owner"] and a["due"] for a in ins["actions"])


def test_headline_not_duplicating_takeaway():
    from types import SimpleNamespace

    from app.agent.rules import fix_headline
    from app.agent.schemas import Claim, Consequences, Insight

    ins = Insight(event_id="e", company_uid="c", headline="Ставка останется высокой дольше — кредит компании дорожает",
                  takeaway=Claim(text="Ставка останется высокой дольше: кредит компании дорожает на 0,5 млн ₽ в год"),
                  what_happened=[], why_it_matters=[], consequences=Consequences(), actions=[], bank_view=[], sources=[],
                  trace_id="t")
    ctx = SimpleNamespace(event=SimpleNamespace(title="Банк России сохранил ключевую ставку 14%"))
    assert fix_headline(ins, ctx).headline == "Банк России сохранил ключевую ставку 14%"


def test_materiality_demotes_in_feed_widget_and_digest(client, demo):
    from app.storage import db

    u, h = demo
    url = f"/api/feed?company_id={u['primary_company']}&role={u['role']}&limit=20"
    items = client.get(url, headers=h).json()["items"]
    top = items[0]["event_id"]
    payload = {"materiality": 0, "materiality_why": "Субсидия в другом регионе", "generated_by": "llm:test"}
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO insights(key, event_id, company_uid, role, payload, trace_id) VALUES (?,?,?,?,?,?)",
                     ("test-materiality", top, u["primary_company"], u["role"], json.dumps(payload), "t"))
    try:
        after = client.get(url, headers=h).json()["items"]
        card = next(x for x in after if x["event_id"] == top)
        assert card["ai_relevance"] == {"level": 0, "why": "Субсидия в другом регионе"}
        assert after[0]["event_id"] != top
        digest = client.get(f"/api/digest?company_id={u['primary_company']}&role={u['role']}", headers=h).json()
        assert all(t["event_id"] != top for t in digest["top"])
    finally:
        with db.tx() as conn:
            conn.execute("DELETE FROM insights WHERE key='test-materiality'")
