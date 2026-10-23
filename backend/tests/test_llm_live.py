"""Живой режим LLM на фейковом провайдере: схемы и «ремонт» JSON, двухуровневая генерация,
allowlist инструментов, бюджет и приоритеты, SSE-стриминг агента и чата."""
import json

import pytest
from fastapi.testclient import TestClient

from app.agent.event_brief import get_event_brief
from app.agent.orchestrator import run_agent
from app.agent.trace import load_trace
from app.bootstrap import load_demo_users
from app.config import get_settings
from app.data.profiles import get_profile
from app.llm import client as llm_client
from app.llm.client import LLMUnavailable, budget_day, get_llm, llm_priority, reset_llm
from app.news.ingest import list_events
from app.ranking.scorer import rank_feed
from app.storage import db

CALLS: list[str] = []


def _resp(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 300}}


def fake_post(self, payload: dict, attempts: int = 3) -> dict:
    sys_msg = payload["messages"][0]["content"] if isinstance(payload["messages"][0]["content"], str) else ""
    last = payload["messages"][-1]["content"]
    if "Классифицируй" in sys_msg:
        CALLS.append("classify")
        return _resp(json.dumps({"summary": "Событие", "event_type": "monetary_policy",
                                 "factors": [{"factor": "key_rate", "direction": "stable", "confidence": 0.9}], "importance": 4}))
    if "Разбери событие ОДИН раз" in sys_msg:
        CALLS.append("brief")
        news_id = json.loads(last.split("<news>\n")[1].split("\n</news>")[0])[0]["id"]
        return _resp(json.dumps({"summary": "Разбор события", "key_facts": [{"text": "Факт", "sources": [news_id]}],
                                 "channels": [{"factor": "key_rate", "direction": "stable", "mechanism": "дорогие деньги"},
                                              {"factor": "bogus_factor", "direction": "up", "mechanism": "x"}],
                                 "bank_alignment": [{"thesis_id": "t01", "stance": "agrees"}]}, ensure_ascii=False))
    if "планировщик" in sys_msg:
        CALLS.append("plan")
        return _resp(json.dumps({"focus": "ставка", "steps": [{"tool": "rm_rf", "args": {}}, {"tool": "get_counterparties"}]}))
    if "редактор-риск-менеджер" in sys_msg:
        CALLS.append("critic")
        return _resp(json.dumps({"verdict": "ok", "issues": [], "scores": {"personalization": 5, "depth": 4, "grounding": 5,
                                                                           "bank_view": 5, "actionability": 4}}))
    if "ассистент Альфа-Бизнеса" in sys_msg:
        CALLS.append("chat")
        return _resp("Позиция Альфа-Банка: пауза до конца года [bank:t01]")
    if "аналитик Альфа-Банка" in sys_msg:
        if "не прошёл проверку" in str(last):  # ремонт после невалидного ответа
            CALLS.append("interpret_repair")
            news_id = next(t for t in json.loads(payload["messages"][1]["content"].split("<context>\n")[1].split("\n</context>")[0])
                           ["get_event"]["news"])["id"]
            claim = {"text": "Деньги остаются дорогими", "kind": "ai_inference", "sources": ["profile:CL_PRODUCT_LOA_MARK", news_id]}
            return _resp(json.dumps({"headline": "Деньги дорогие дольше", "takeaway": claim,
                                     "what_happened": [{**claim, "kind": "fact", "sources": [news_id]}],
                                     "why_it_matters": [claim], "consequences": {"risks": [claim]},
                                     "actions": [{**claim, "action_type": "finance"}],
                                     "bank_view": [{"text": "Банк ждёт паузы", "kind": "bank_view", "sources": ["bank:t01"]}]},
                                    ensure_ascii=False))
        CALLS.append("interpret_invalid")
        return _resp('{"headline": "без обязательных полей"}')
    raise AssertionError(f"неожиданный запрос: {sys_msg[:80]}")


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setattr(llm_client.LLMClient, "_post", fake_post)
    get_settings.cache_clear()
    reset_llm()
    CALLS.clear()
    yield get_llm()
    monkeypatch.setenv("LLM_MODE", "mock")
    get_settings.cache_clear()
    reset_llm()


def _persona():
    u = load_demo_users()[0]
    p = get_profile(u["primary_company"])
    ev = rank_feed(list_events(), p, u["role"])[0].event
    return u, p, ev


def test_agent_live_with_repair_allowlist_and_brief(live):
    u, p, ev = _persona()
    db.query("DELETE FROM event_briefs WHERE event_id=?", (ev.id,))
    ins = run_agent(ev, p, u["role"])
    assert ins.generated_by.startswith("llm:")
    assert ins.scores["personalization"] == 5
    assert "interpret_invalid" in CALLS and "interpret_repair" in CALLS  # невалидный JSON отремонтирован
    steps = {s["name"]: s for s in load_trace(ins.trace_id)}
    assert "tool:get_event_brief" in steps and steps["tool:get_event_brief"]["model"]
    assert "tool:rm_rf" not in steps  # неизвестный инструмент из плана не выполняется
    brief, by, _ = get_event_brief(ev)
    assert by.startswith("llm:") and all(c.factor != "bogus_factor" for c in brief.channels)
    assert all(c.passed for c in ins.quality), [c for c in ins.quality if not c.passed]
    spent = db.query_one("SELECT SUM(cost_rub) s FROM llm_usage WHERE day=?", (budget_day(),))["s"]
    assert spent and spent > 0


def test_background_priority_keeps_reserve(live):
    bs = live.budget_status()
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO llm_usage(day, model, calls, tokens_in, tokens_out, cost_rub) VALUES (?,?,?,?,?,?)",
                     (budget_day(), "test-reserve", 1, 0, 0, bs["background_limit_rub"]))
    try:
        msgs = [{"role": "user", "content": "фоновая задача"}]
        with llm_priority("background"), pytest.raises(LLMUnavailable):
            live.complete("chat", msgs)
        assert live.complete("chat", [{"role": "system", "content": "ассистент Альфа-Бизнеса"}, *msgs]).text
    finally:
        with db.tx() as conn:
            conn.execute("DELETE FROM llm_usage WHERE model='test-reserve'")


def test_sse_streams_agent_steps_and_chat(live):
    from app.main import app

    u, p, ev = _persona()
    h = {"X-User-Id": u["user_id"]}
    with TestClient(app) as c:
        with c.stream("GET", f"/api/insights/{ev.id}/stream?company_id={p.uid}&role=owner&refresh=true", headers=h) as r:
            events = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
        types = [e["type"] for e in events]
        assert "step_start" in types and "step_end" in types and types[-1] == "done"
        # черновик модели приходит до итогового разбора — его можно читать, пока работают критик и правка
        assert "draft" in types and types.index("draft") < types.index("done")
        draft = events[types.index("draft")]["insight"]
        assert draft["headline"] and draft["generated_by"].startswith("llm:") and draft["sources"]
        assert events[-1]["insight"]["generated_by"].startswith("llm:")
        with c.stream("POST", "/api/chat/stream", headers=h,
                      json={"event_id": ev.id, "company_id": p.uid, "role": "owner", "question": "Что говорит банк?"}) as r:
            chat = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
        text = "".join(e["text"] for e in chat if e["type"] == "delta")
        assert chat[0]["type"] == "meta" and "Альфа-Банка" in text and chat[-1]["type"] == "done"
        st = c.get("/api/llm/status").json()
        assert st["mode"] == "live" and "event_brief" in st["tasks"]


def test_reasoning_model_extras_and_empty_content_retry(live, monkeypatch):
    import httpx

    seen = []

    def post(self, payload, attempts=3):
        seen.append(payload)
        if "reasoning" in payload:  # эндпоинт не принимает параметр — клиент должен повторить без него
            req = httpx.Request("POST", "http://llm.invalid/v1/chat/completions")
            raise httpx.HTTPStatusError("bad", request=req, response=httpx.Response(400, request=req))
        if payload["max_tokens"] < 1000:  # «думающая» модель исчерпала лимит на рассуждения
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        return {"choices": [{"message": {"content": "ОК"}, "finish_reason": "stop"}], "usage": {}}

    monkeypatch.setattr(llm_client.LLMClient, "_post", post)
    res = live.complete("chat", [{"role": "user", "content": "проверка думающей модели"}], max_tokens=50, model_key="glm")
    assert res.text == "ОК"
    assert any("reasoning" in p for p in seen) and seen[-1]["max_tokens"] >= 1000


def test_provider_spend_wins_over_local_estimate(live, monkeypatch):
    """Локальная оценка расхода (переносится снапшотом между прогонами) не должна блокировать модели,
    если провайдер видит реальный расход меньше."""
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO llm_usage(day, model, calls, tokens_in, tokens_out, cost_rub) VALUES (?,?,?,?,?,?)",
                     (budget_day(), "test-local", 1, 0, 0, 450))
    try:
        monkeypatch.setattr(llm_client.LLMClient, "provider_budget", lambda self, max_age_s=60.0: {"spent": 100.0, "max_budget": 500})
        b = live.budget_status()
        assert b["source"] == "provider" and b["spent_rub"] == 100.0
        with llm_priority("background"):
            assert live.model_for("interpret", "background")
    finally:
        with db.tx() as conn:
            conn.execute("DELETE FROM llm_usage WHERE model='test-local'")


def test_refresh_does_not_replace_llm_insight_with_rules(live, monkeypatch):
    from app.agent import orchestrator

    u, p, ev = _persona()
    good = orchestrator.get_or_create_insight(ev, p, u["role"], force=True)
    assert good.generated_by.startswith("llm:")

    def rules_only(*a, **k):
        raise LLMUnavailable("бюджет")

    monkeypatch.setattr(llm_client.LLMClient, "_post", rules_only)
    again = orchestrator.get_or_create_insight(ev, p, u["role"], force=True)
    assert again.generated_by == good.generated_by
    assert orchestrator.cached_insight(ev.id, p.uid, u["role"]).generated_by.startswith("llm:")


def test_revise_only_when_critic_has_remarks(live, monkeypatch):
    """Критик доволен — правки моделью нет (решение в трассировке); критик недоволен — правка есть."""
    u, p, ev = _persona()
    ins = run_agent(ev, p, u["role"])
    steps = {s["name"]: s for s in load_trace(ins.trace_id)}
    assert "revise" not in CALLS and "revise" not in steps
    if "decide:revise" in steps:
        assert steps["decide:revise"]["output"]["revise_with_llm"] is False

    def picky(self, payload, attempts=3):
        sys_msg = payload["messages"][0]["content"]
        if "редактор-риск-менеджер" in sys_msg:
            CALLS.append("critic")
            return _resp(json.dumps({"verdict": "revise", "issues": ["нет сроков у действий"],
                                     "scores": {"personalization": 4, "depth": 3, "grounding": 4, "bank_view": 4,
                                                "actionability": 3}}, ensure_ascii=False))
        return fake_post(self, payload, attempts)

    monkeypatch.setattr(llm_client.LLMClient, "_post", picky)
    with db.tx() as conn:  # иначе критик ответит из кэша первого прогона
        conn.execute("DELETE FROM llm_cache")
    CALLS.clear()
    ins = run_agent(ev, p, u["role"])
    steps = {s["name"]: s for s in load_trace(ins.trace_id)}
    assert steps["decide:revise"]["output"]["revise_with_llm"] is True and "revise" in steps


def test_llm_deadline_cuts_calls():
    """Остаток времени агента — потолок для HTTP-таймаута: без времени модель не вызывается вовсе."""
    from types import SimpleNamespace

    from app.llm.client import llm_deadline, time_left

    dummy = SimpleNamespace(settings=SimpleNamespace(llm_base_url="http://llm.invalid/v1", llm_timeout_s=60),
                            _headers=lambda: {})
    with llm_deadline(1):
        assert 0 < time_left() <= 1
        with pytest.raises(LLMUnavailable):
            llm_client.LLMClient._post(dummy, {})
    assert time_left() is None
