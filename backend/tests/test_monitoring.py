"""Этап 9: события интерфейса, продуктовые метрики, алерты и проверка качества в PR."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def demo(client):
    u = client.get("/api/demo-users").json()["users"][0]
    return u, {"X-User-Id": u["user_id"]}


def test_track_filters_types_and_foreign_companies(client, demo):
    from app.storage import db

    u, h = demo
    foreign = db.query_one("SELECT uid FROM companies WHERE uid NOT IN (SELECT uid FROM user_links WHERE fid=?) LIMIT 1",
                           (u["user_id"],))["uid"]
    events = [
        {"type": "home_view", "company_id": u["primary_company"]},
        {"type": "widget_click", "company_id": u["primary_company"], "event_id": "e1"},
        {"type": "card_open", "company_id": u["primary_company"], "event_id": "e1", "meta": {"from": "widget"}},
        {"type": "preview_ready", "company_id": u["primary_company"], "event_id": "e1", "value": 420},
        {"type": "insight_ready", "company_id": u["primary_company"], "event_id": "e1", "value": 30000, "meta": {"cached": False}},
        {"type": "drop_table", "company_id": u["primary_company"]},  # неизвестный тип
        {"type": "home_view", "company_id": foreign},  # чужая компания
    ]
    r = client.post("/api/track", headers=h, json={"events": events})
    assert r.status_code == 200 and r.json()["stored"] == 5


def test_track_requires_auth_and_limits_batch(client, demo):
    assert client.post("/api/track", json={"events": [{"type": "home_view"}]}).status_code == 401
    _, h = demo
    assert client.post("/api/track", headers=h, json={"events": [{"type": "home_view"}] * 51}).status_code == 422


def test_metrics_product_block(client, demo):
    _, h = demo
    m = client.get("/api/metrics?days=7", headers=h).json()
    p = m["product"]
    assert p["funnel"]["home_views"] >= 1 and p["funnel"]["widget_clicks"] >= 1
    assert p["funnel"]["opened_from"].get("widget", 0) >= 1
    assert p["rates"]["widget_ctr"] is not None
    assert p["speed_ms"]["preview_p50"] is not None and p["speed_ms"]["full_fresh_p50"] is not None
    assert {"insights", "avg_rub"} <= set(m["cost"])
    assert m["offline"]["relevance"]["pairs"] >= 300
    assert isinstance(m["alerts"], list)
    assert client.get("/api/metrics?days=0", headers=h).status_code == 422


def test_alerts_rules():
    from app import monitoring

    product = {"feedback": {"pos": 2, "neg": 4}, "speed_ms": {"preview_p95": 3500}}
    out = monitoring.alerts({"budget_rub": 500, "spent_rub": 450}, product, {"avg": 3.6}, 2, 9)
    text = " ".join(a["text"] for a in out)
    for frag in ("бюджета", "👎", "p95", "Источников", "injection", "судьи"):
        assert frag in text
    calm = monitoring.alerts({"budget_rub": 500, "spent_rub": 10}, {"feedback": {"pos": 9, "neg": 1}, "speed_ms": {"preview_p95": 300}},
                             {"avg": 4.5}, 0, 0)
    assert calm == []


def _gate(tmp_path, cur: dict, base: dict):
    (tmp_path / "cur.json").write_text(json.dumps(cur), encoding="utf-8")
    (tmp_path / "base.json").write_text(json.dumps(base), encoding="utf-8")
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "quality_gate.py"), str(tmp_path / "cur.json"),
                           str(tmp_path / "base.json"), "--md", str(tmp_path / "r.md")], capture_output=True, text=True)


def test_quality_gate_blocks_regression(tmp_path):
    base = {"relevance": {"p3": 0.95, "ndcg5": 0.85}, "dedup": {"precision": 0.92}}
    ok = _gate(tmp_path, {"relevance": {"p3": 0.93, "ndcg5": 0.86}, "dedup": {"precision": 0.92}}, base)
    assert ok.returncode == 0, ok.stdout
    bad = _gate(tmp_path, {"relevance": {"p3": 0.80, "ndcg5": 0.86}, "dedup": {"precision": 0.92}}, base)
    assert bad.returncode == 1 and "Регрессия" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_baseline_meets_gate_against_itself(tmp_path):
    base = json.loads((ROOT.parent / "docs" / "quality" / "baseline.json").read_text(encoding="utf-8"))
    assert _gate(tmp_path, base, base).returncode == 0
