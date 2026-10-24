"""Неочевидные последствия и ИИ-сито ленты: вторая модель отсекает натянутое, без моделей ничего не меняется."""
from types import SimpleNamespace

from app.agent.schemas import Claim, Consequences, Insight
from app.agent.trace import Tracer
from app.llm.client import LLMResult
from app.news.models import Event, EventClassification, FactorHit


class FakeLLM:
    enabled = True

    def __init__(self, answers):
        self.answers = answers

    def complete_json(self, task, messages, schema=None, **kw):
        data = self.answers[task]
        return (schema.model_validate(data).model_dump() if schema else data), LLMResult(text="", model=f"fake/{task}")


def _profile():
    from app.bootstrap import load_demo_users
    from app.data.profiles import get_profile

    return get_profile(load_demo_users()[0]["primary_company"])


def _ins():
    return Insight(event_id="e1", company_uid="U_1", role=None, headline="h", takeaway=Claim(text="t"), what_happened=[],
                   why_it_matters=[Claim(text="Ставка дорожает")], consequences=Consequences(), actions=[], bank_view=[],
                   sources=[], trace_id="x")


def test_hidden_effects_only_verified_and_grounded(monkeypatch):
    from app.agent import hidden

    p = _profile()
    links = hidden.company_links(p)
    assert links and all(x["label"] and ":" in x["id"] for x in links)
    real = links[0]["id"]
    fake = FakeLLM({
        "hidden_effects": {"items": [
            {"text": "Покупатели отложат заказы", "effect": "risk", "chain": ["Ставка выше", "Покупатели экономят", "Меньше заказов"],
             "via": [real], "plausibility": 3},
            {"text": "Натянутое", "effect": "opportunity", "chain": ["a", "b"], "via": [real], "plausibility": 1},
            {"text": "Чужая связь", "effect": "risk", "chain": ["a", "b"], "via": ["graph:in:99"], "plausibility": 3},
            {"text": "Проверяющий против", "effect": "opportunity", "chain": ["a", "b", "c"], "via": [real], "plausibility": 2}]},
        "hidden_check": {"items": [{"n": 1, "keep": True, "why": "связь есть"}, {"n": 2, "keep": False, "why": "натяжка"}]},
    })
    monkeypatch.setattr(hidden, "get_llm", lambda: fake)
    ev = Event(id="e1", title="ЦБ повысил ставку", published_at="2026-10-24", news=[],
               classification=EventClassification(summary="Ставка 17%", factors=[FactorHit(factor="key_rate", direction="up")]))
    out = hidden.find_hidden(_ins(), SimpleNamespace(event=ev, profile=p), Tracer())
    assert [h.text for h in out] == ["Покупатели отложат заказы"]  # натянутое, чужая связь и отклонённое проверяющим — убраны
    assert out[0].sources == [real] and out[0].verified_by == "hidden_check"


def test_screen_hides_only_when_both_models_agree(monkeypatch):
    from app.ranking import screen

    p = _profile()
    mk = lambda i, score, reasons=(): SimpleNamespace(score=score, reasons=list(reasons),  # noqa: E731
                                                      event=SimpleNamespace(id=f"scr{i}", title=f"Новость {i}",
                                                                            classification=SimpleNamespace(summary="")))
    top = [mk(1, 0.3), mk(2, 0.3), mk(3, 1.5), mk(4, 0.3, [{"factor": "watch"}]), mk(5, 0.4)]
    got = {1: {"relevance": 0, "why": "не про компанию"}, 2: {"relevance": 0, "why": "не про компанию"},
           3: {"relevance": 0, "why": ""}, 4: {"relevance": 0, "why": ""}, 5: {"relevance": 2, "why": "важно"}}
    fake = FakeLLM({"screen_check": {"items": [{"n": 1, "relevant": False, "why": "канала нет"}, {"n": 2, "relevant": True, "why": "кредит"}]}})
    monkeypatch.setattr(screen, "get_llm", lambda: fake)
    hidden = screen.verify_and_store(top, got, p, "scr-role")
    assert set(hidden) == {"scr1"}  # 2 — вторая модель против; 3 — высокая важность; 4 — пользователь следит
    assert screen.screened(p.uid, "scr-role") == {"scr1": "канала нет"}
