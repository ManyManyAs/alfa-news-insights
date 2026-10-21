"""Этап 5: персонализация v2 — обучение на обратной связи, ручные интересы, разнообразие, LLM-реранк, «тишина»."""
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


def _feed(client, demo):
    u, h = demo
    return client.get(f"/api/feed?company_id={u['primary_company']}&role={u['role']}", headers=h).json()


def test_mute_and_reset_preferences(client, demo):
    u, h = demo
    client.delete(f"/api/companies/{u['primary_company']}/preferences", headers=h)  # 👍 из других тестов тоже поправки
    items = _feed(client, demo)["items"]
    top_factor = next(r["factor"] for r in items[0]["reasons"] if r["factor"] not in ("country", "industry", "region"))
    r = client.put(f"/api/companies/{u['primary_company']}/preferences", headers=h, json={"factor": top_factor, "level": "mute"})
    assert r.status_code == 200
    after = _feed(client, demo)["items"]
    assert all(r["factor"] != top_factor for it in after for r in it["reasons"]), "скрытая тема не должна давать вклад"
    prof = client.get(f"/api/companies/{u['primary_company']}", headers=h).json()
    assert any(o["factor"] == top_factor and o["multiplier"] == 0 for o in prof["overrides"])
    assert client.delete(f"/api/companies/{u['primary_company']}/preferences", headers=h).status_code == 200
    restored = _feed(client, demo)["items"]
    assert [i["score"] for i in restored[:3]] == [i["score"] for i in items[:3]]  # порядок равных по скору может меняться
    # чужая компания — нельзя
    assert client.put("/api/companies/U_NOT_MINE/preferences", headers=h, json={"factor": "key_rate", "level": "mute"}).status_code in (403, 404)


def test_feedback_lowers_factor_and_explains(client, demo):
    u, h = demo
    items = _feed(client, demo)["items"]
    ev = items[0]["event_id"]
    r = client.post("/api/feedback", headers=h, json={"event_id": ev, "company_id": u["primary_company"], "role": u["role"],
                                                       "rating": -1, "reason": "not_relevant"}).json()
    assert r["changed"] and all(c["multiplier"] < 1 for c in r["changed"])
    after = _feed(client, demo)["items"]
    card = next((i for i in after if i["event_id"] == ev), None)
    if card:  # событие может уйти из ленты целиком — это тоже ожидаемо
        assert card["score"] < items[0]["score"]
        assert any(r.get("personal") for r in card["reasons"])
    # «уже знал» на веса не влияет
    r2 = client.post("/api/feedback", headers=h, json={"event_id": ev, "company_id": u["primary_company"], "rating": -1,
                                                        "reason": "known"}).json()
    assert r2["changed"] == []
    client.delete(f"/api/companies/{u['primary_company']}/preferences", headers=h)


def test_mmr_diversifies():
    from app.ranking.scorer import Scored, mmr, primary_factor

    def s(eid, score, f):
        class E:
            id = eid
            story_id = None
        return Scored(event=E(), score=score, effect="watch",
                      reasons=[{"factor": f, "contribution": score}], matched_factors=[f])

    out = mmr([s("a", 1.0, "key_rate"), s("b", 0.95, "key_rate"), s("c", 0.8, "fx_rub")], 3)
    assert [primary_factor(x) for x in out][:2] == ["key_rate", "fx_rub"]


def test_rerank_with_fake_llm(monkeypatch):
    from app.data.models import CompanyProfile
    from app.news.ingest import list_events
    from app.ranking import rerank as rr
    from app.ranking.scorer import rank_feed

    prof = CompanyProfile(uid="T", okved="56.10", okved_name="Рестораны", products=["CL_PRODUCT_LOA_MARK"])
    items = rank_feed(list_events(100), prof, None, 6)
    assert len(items) >= 3
    last = len(items[:rr.TOP_N])

    class Res:
        model = "fake"
        cached = False

    class FakeLLM:
        enabled = True

        def complete_json(self, task, msgs, schema, **k):
            assert task == "rerank" and "<news>" in msgs[1]["content"]
            return {"items": [{"n": i, "relevance": 3 if i == last else 0, "why": "тест"} for i in range(1, last + 1)]}, Res()

    monkeypatch.setattr(rr, "get_llm", lambda: FakeLLM())
    out = rr.rerank(items, prof, None)
    assert out["applied"] and out["order"][0] == items[last - 1].event.id
    assert set(out["order"]) == {s.event.id for s in items}


def test_rerank_without_llm_is_noop(client, demo):
    u, h = demo
    r = client.get(f"/api/feed/rerank?company_id={u['primary_company']}", headers=h).json()
    assert r["applied"] is False


def test_quiet_flag_present(client, demo):
    assert "quiet" in _feed(client, demo)
