"""Прогрев разборов после сбора новостей: только с живыми моделями, в рамках фонового бюджета, без повторов."""
from types import SimpleNamespace

import app.agent.orchestrator as orch
import app.llm.client as llm_client
from app.agent.prewarm import prewarm_top
from app.llm.client import LLMUnavailable


class FakeLLM:
    live = True
    mode = "live"

    def __init__(self, background_ok: bool = True):
        self.spent = 0.0
        self.background_ok = background_ok

    def budget_status(self):
        return {"spent_rub": self.spent}

    def model_for(self, task, priority="interactive"):
        if priority == "background" and not self.background_ok:
            raise LLMUnavailable("Резерв бюджета оставлен для запросов пользователей")
        return "deepseek", {}


def test_prewarm_skips_without_live_models():
    # тесты идут в mock-режиме: прогрев ничего не тратит и ничего не строит
    assert "skipped" in prewarm_top()


def test_prewarm_builds_top_once(monkeypatch):
    fake = FakeLLM()
    store: dict = {}
    calls: list = []

    def cached(event_id, uid, role):
        return store.get((event_id, uid, role))

    def build(event, profile, role, force=False, listener=None):
        calls.append((event.id, profile.uid, role, force))
        fake.spent += 0.8
        ins = SimpleNamespace(generated_by="llm:deepseek")
        store[(event.id, profile.uid, role)] = ins
        return ins

    monkeypatch.setattr(llm_client, "get_llm", lambda: fake)
    monkeypatch.setattr(orch, "cached_insight", cached)
    monkeypatch.setattr(orch, "get_or_create_insight", build)

    first = prewarm_top(top=2, max_rub=100, max_seconds=60)
    assert first["stopped"] is None
    assert first["built"] == len(calls) > 0
    # второй проход: всё уже готово — ни одного вызова модели
    n = len(calls)
    second = prewarm_top(top=2, max_rub=100, max_seconds=60)
    assert len(calls) == n and second["ready"] == n and second["built"] == 0


def test_prewarm_respects_pass_limit_and_background_budget(monkeypatch):
    fake = FakeLLM()
    calls: list = []

    def build(event, profile, role, force=False, listener=None):
        calls.append(event.id)
        fake.spent += 5.0
        return SimpleNamespace(generated_by="llm:deepseek")

    monkeypatch.setattr(llm_client, "get_llm", lambda: fake)
    monkeypatch.setattr(orch, "cached_insight", lambda *a: None)
    monkeypatch.setattr(orch, "get_or_create_insight", build)
    res = prewarm_top(top=3, max_rub=10, max_seconds=60)
    assert len(calls) == 2 and res["stopped"]  # 2 × 5 ₽ — дальше лимит прохода

    calls.clear()
    monkeypatch.setattr(llm_client, "get_llm", lambda: FakeLLM(background_ok=False))
    res = prewarm_top(top=3, max_rub=10, max_seconds=60)
    assert calls == [] and "Резерв" in res["stopped"]
