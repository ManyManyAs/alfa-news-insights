"""Этап 6: агент v2 — сценарии, «что если», триггеры, быстрый первый вывод, ограничители."""
import time

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


def test_calc_formulas():
    from app.agent.scenario import Inputs, calc
    from app.data.models import CompanyProfile

    p = CompanyProfile(uid="X", okved="41.20", okved_name="Стройка", products=[], revenue=1e9, workers="16-100")
    r = calc(p, Inputs(debt_rub=100e6, floating_share=0.5, rate_delta_pp=1, payroll_rub=50e6,
                       planned_wage_growth_pct=10, wage_growth_pct=12))
    rows = {x["lever"]: x["effect_rub"] for x in r["rows"]}
    assert rows["rate"] == -500_000  # 100 млн × 50% × 1 п.п.
    assert rows["wages"] == -1_000_000  # 50 млн × (12% − 10%)
    assert r["total_rub"] == -1_500_000 and r["share_of_revenue"] == -0.0015
    fx = calc(p, Inputs(import_rub=200e6, pass_through=0.25, fx_change_pct=10))
    assert {x["lever"]: x["effect_rub"] for x in fx["rows"]}["fx"] == -15_000_000  # 200 млн × 10% × 75%


def test_scenarios_anchor_on_bank_forecast():
    from app.agent.scenario import bank_baseline, scenarios
    from app.data.models import CompanyProfile

    p = CompanyProfile(uid="X", okved="41.20", okved_name="Стройка", products=["CL_PRODUCT_LOA_MARK"], revenue=1e9)
    sc = {s["name"]: s for s in scenarios(p, ["key_rate"])}
    assert set(sc) == {"base", "negative", "positive"}
    assert sc["base"]["params"]["rate_delta_pp"] == bank_baseline()["rate_delta_pp"]
    assert any(r.startswith("bank:") for r in sc["base"]["sources"])
    assert sc["negative"]["total_rub"] < sc["base"]["total_rub"] < sc["positive"]["total_rub"]


def test_agent_v2_insight_and_preview():
    from app.agent.orchestrator import run_agent
    from app.bootstrap import load_demo_users
    from app.data.profiles import get_profile
    from app.news.ingest import list_events

    u = load_demo_users()[0]
    p = get_profile(u["primary_company"])
    ev = next(e for e in list_events(100) if e.classification.effective_dates)
    events = []
    ins = run_agent(ev, p, u["role"], listener=events.append)
    types = [e["type"] for e in events]
    assert "preview" in types and types.index("preview") < [e.get("name") for e in events].index("draft")
    assert len(ins.scenarios) == 3 and ins.what_could_change
    assert any(s.type == "calc" for s in ins.sources) or not any("calc:scenario" in c.sources for c in ins.actions)
    steps = [e.get("name") for e in events if e["type"] == "step_start"]
    assert "tool:scenario_calc" in steps and "tool:get_regulation_dates" in steps
    assert all(c.passed for c in ins.quality)


def test_limits_stop_llm():
    from types import SimpleNamespace

    from app.agent import orchestrator as orc
    from app.agent.trace import Tracer

    Ctx = SimpleNamespace(memory={"_t0": time.perf_counter() - orc.AGENT_TIMEOUT_S - 1})
    tr = Tracer()
    assert orc.llm_allowed(Ctx, tr) is False
    assert tr.steps[-1]["name"] == "limit" and "время" in tr.steps[-1]["output"]["stop_llm"]
    assert orc.llm_allowed(Ctx, tr) is False and len([s for s in tr.steps if s["name"] == "limit"]) == 1


def test_scenario_api(client, demo):
    u, h = demo
    r = client.post("/api/scenario", headers=h, json={"company_id": u["primary_company"],
                                                      "inputs": {"debt_rub": 200e6, "floating_share": 1, "rate_delta_pp": 2}}).json()
    rate = next(x for x in r["result"]["rows"] if x["lever"] == "rate")
    dep = r["result"]["values"].get("deposits_rub") or 0
    assert rate["effect_rub"] == round(-200e6 * 0.02 + dep * 0.02)
    assert "debt_rub" in r["result"]["user_set"] and len(r["scenarios"]) == 3
    assert client.post("/api/scenario", headers=h, json={"company_id": "U_NOT_MINE"}).status_code in (403, 404)


def test_cached_insight_gets_scenarios(client, demo):
    u, h = demo
    ev = client.get(f"/api/feed?company_id={u['primary_company']}", headers=h).json()["items"][0]["event_id"]
    ins = client.get(f"/api/insights/{ev}?company_id={u['primary_company']}", headers=h).json()
    assert len(ins["scenarios"]) == 3
    assert "judge" in client.get("/api/metrics").json()["insights"]


def test_digest(client, demo):
    u, h = demo
    for period in ("week", "day"):
        d = client.get(f"/api/digest?company_id={u['primary_company']}&role=cfo&period={period}", headers=h).json()
        assert d["role_title"] == "Финансовый директор" and "top" in d and "dates" in d
        assert all(x["days"] >= 0 for x in d["dates"])
    assert client.get(f"/api/digest?company_id={u['primary_company']}&period=year", headers=h).status_code == 422
    assert client.get("/api/digest?company_id=U_NOT_MINE", headers=h).status_code in (403, 404)


def test_index_versions_all_static(client):
    html = client.get("/").text
    for name in ("viz.js", "tour.js", "app.js", "styles.css"):
        assert f"/static/{name}?v=" in html
