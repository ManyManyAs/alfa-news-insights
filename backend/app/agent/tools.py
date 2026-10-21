"""Инструменты агента. Только чтение; список разрешённых инструментов фиксирован (allowlist).

Каждый вызов журналируется в трассировку. Инструменты получают контекст текущего запроса
(компания, на которую у пользователя уже проверены права), поэтому агент не может
прочитать данные другой компании, даже если LLM попросит.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import load_yaml
from app.data.models import CompanyProfile
from app.data.sensitivity import Exposure
from app.knowledge.bank import get_knowledge
from app.news.dedup import jaccard, stems
from app.news.models import Event
from app.ranking.scorer import Scored

MAX_TOOL_CALLS = 14


@dataclass
class AgentContext:
    event: Event
    profile: CompanyProfile
    role: str | None
    exposures: dict[str, Exposure]
    scored: Scored
    memory: dict = field(default_factory=dict)  # результаты инструментов — общий контекст шагов
    tool_calls: int = 0


def t_get_company_profile(ctx: AgentContext) -> dict:
    top = sorted(ctx.exposures.values(), key=lambda e: e.weight, reverse=True)[:8]
    return {"profile": ctx.profile.llm_view(),
            "top_exposures": [{"factor": e.factor, "weight": round(e.weight, 2), "sign": e.sign,
                               "why": [r["why"] for r in e.reasons][:3]} for e in top]}


def t_get_event(ctx: AgentContext) -> dict:
    c = ctx.event.classification
    return {"title": ctx.event.title, "published_at": ctx.event.published_at, "summary": c.summary,
            "facts": c.facts, "forecasts_or_opinions": c.forecasts_or_opinions, "numbers": c.numbers,
            "factors": [f.model_dump() for f in c.factors], "sources": ctx.event.sources,
            "security_flags": c.security_flags}


def t_get_event_brief(ctx: AgentContext) -> dict:
    """Общий для всех клиентов разбор события (уровень 1, кэшируется)."""
    from app.agent.event_brief import get_event_brief

    fut = ctx.memory.pop("_brief_future", None)  # разбор мог уже строиться параллельно с планом
    try:
        brief, by, res = fut.result() if fut is not None else get_event_brief(ctx.event)
    except Exception:  # сбой фонового разбора: строим заново (из кэша или правилами)
        brief, by, res = get_event_brief(ctx.event)
    ctx.memory["_brief_llm"] = res  # для трассировки (модель, токены, стоимость)
    return {"generated_by": by, **brief.model_dump()}


def t_get_bank_theses(ctx: AgentContext, factors: list[str] | None = None, include_stale: bool = False) -> dict:
    factors = factors or ctx.scored.matched_factors
    kb = get_knowledge()
    theses = kb.theses_for(factors, include_stale=include_stale, query=f"{ctx.event.title} {ctx.event.classification.summary}")
    return {"theses": [{**t, "doc_title": kb.doc(t["doc_id"]).get("title"), "date": kb.doc(t["doc_id"]).get("date")}
                       for t in theses[:8]]}


def t_get_bank_forecast(ctx: AgentContext, factors: list[str] | None = None, indicator: str | None = None) -> dict:
    """Числовые прогнозы банка (ставка, инфляция, курс, ВВП…) с историей пересмотров."""
    kb = get_knowledge()
    rows = [kb.forecast(indicator, p) for p in ("2026", "2027")] if indicator \
        else kb.forecasts_for(factors or ctx.scored.matched_factors)
    rows = [r for r in rows if r]
    return {"forecasts": [{k: r[k] for k in ("ref", "title", "period", "value", "unit", "date", "revision", "text")} for r in rows[:10]],
            "rate_path": kb.rate_path}


def t_search_bank_analytics(ctx: AgentContext, query: str | None = None, k: int = 5) -> dict:
    q = query or f"{ctx.event.title} {ctx.event.classification.summary}"
    return {"query": q, "results": get_knowledge().search(q, k=k)}


def t_search_related_news(ctx: AgentContext, k: int = 3) -> dict:
    from app.news.ingest import list_events  # локальный импорт: избегаем циклов

    base = stems(ctx.event.title + " " + ctx.event.classification.summary)
    my_factors = {f.factor for f in ctx.event.classification.factors}
    cands = []
    for e in list_events(300):
        if e.id == ctx.event.id:
            continue
        sim = jaccard(base, stems(e.title + " " + e.classification.summary))
        overlap = len(my_factors & {f.factor for f in e.classification.factors})
        score = sim + 0.1 * overlap
        if score > 0.15:
            cands.append((score, e))
    cands.sort(key=lambda x: x[0], reverse=True)
    return {"related": [{"event_id": e.id, "title": e.title, "published_at": e.published_at,
                         "summary": e.classification.summary, "sources": e.sources} for _, e in cands[:k]]}


def t_get_peer_benchmark(ctx: AgentContext) -> dict:
    """Сравнение с похожими компаниями (та же отрасль и масштаб): продукты, концентрация покупателей."""
    from app.data.peers import peer_benchmark

    return peer_benchmark(ctx.profile) or {"note": "группа сравнения не найдена"}


def t_get_counterparties(ctx: AgentContext) -> dict:
    p = ctx.profile
    return {"industry_mix": [{**m, "basis": f"graph:{m['direction']}:{m['okved2']}"} for m in p.industry_mix],
            "unknown_share_in": p.unknown_share_in, "unknown_share_out": p.unknown_share_out,
            "hhi_buyers": p.hhi_in, "hhi_suppliers": p.hhi_out, "n_buyers": p.n_buyers, "n_suppliers": p.n_suppliers,
            "network_percentile": p.network_pct, "affiliated_companies": len(p.affiliates),
            "top_counterparties": [
        {"role": "покупатель" if c.direction == "in" else "поставщик", "okved": c.okved, "okved_name": c.okved_name,
         "share": round(c.share, 3), "share_pct": f"{c.share:.0%}", "basis": f"graph:{c.direction}:{c.okved[:2]}"}
        for c in ctx.profile.counterparties if c.okved],  # без ОКВЭД ссылку graph:<...> построить нельзя
        "top_buyer_share": ctx.profile.top_buyer_share, "top_supplier_share": ctx.profile.top_supplier_share}


def t_get_playbook(ctx: AgentContext, factors: list[str] | None = None) -> dict:
    pb = load_yaml("playbook.yaml")["factors"]
    factors = factors or ctx.scored.matched_factors
    return {f: pb[f] for f in factors if f in pb}


def t_check_consistency(ctx: AgentContext) -> dict:
    """Проверка противоречий: разные числа в источниках одного события, расхождение с позицией банка."""
    import re

    issues = []
    nums_by_src = {}
    for n in ctx.event.news:
        nums_by_src[n.id] = set(re.findall(r"\d+(?:[.,]\d+)?\s?%", n.title + " " + n.text))
    if len(nums_by_src) > 1:
        all_sets = list(nums_by_src.values())
        if all(all_sets) and not set.intersection(*all_sets):
            issues.append("Источники события приводят разные числовые значения — требуется сверка")
    # Сверяем только с тезисами, которые агент уже видит в контексте (иначе модель ссылается на «чужие» id)
    seen = ctx.memory.get("get_bank_theses") or t_get_bank_theses(ctx)
    theses = seen.get("theses", [])
    dir_ru = {"up": "рост", "down": "снижение"}
    divergences = []
    for hit in ctx.event.classification.factors:
        for t in theses:
            if t["factor"] == hit.factor and hit.direction in dir_ru and t["direction"] in dir_ru \
                    and hit.direction != t["direction"]:
                divergences.append({"thesis_ref": f"bank:{t['id']}", "factor": hit.factor,
                                    "event_direction": hit.direction, "bank_direction": t["direction"],
                                    "bank_horizon": t.get("horizon"), "bank_thesis": t["thesis"]})
                issues.append(f"Новость говорит о «{dir_ru[hit.direction]}» по фактору {hit.factor}, а тезис банка "
                              f"bank:{t['id']} ({t.get('horizon') or 'горизонт не указан'}) — о «{dir_ru[t['direction']]}». "
                              f"Возможно, это разные горизонты; покажите оба взгляда с этой оговоркой")
                break
    return {"issues": issues, "divergences": divergences}


def t_get_story_timeline(ctx: AgentContext) -> dict:
    """Сюжет события: что было до и после (решение → реакция рынка → прогнозы)."""
    from app.news.stories import get_story

    if not ctx.event.story_id:
        return {"note": "событие не входит в сюжет"}
    st = get_story(ctx.event.story_id) or {}
    return {"title": st.get("title"), "events": [{k: e[k] for k in ("event_id", "title", "published_at", "summary",
                                                                   "factor", "direction")} for e in st.get("events", [])]}


def t_get_regulation_dates(ctx: AgentContext) -> dict:
    """Даты вступления в силу и сколько до них дней — для регуляторных событий."""
    from app.agent.rules import due_text, upcoming_date

    dates = ctx.event.classification.effective_dates
    up = upcoming_date(dates)
    return {"effective_dates": dates, "next": ({"date": up[0].isoformat(), "days": up[1], "text": due_text(up[1])}
                                               if up else None)}


def t_scenario_calc(ctx: AgentContext) -> dict:
    """Сценарии на 12 мес. (база — прогноз Альфа-Банка) с эффектом в ₽ по оценкам профиля. Числа — только отсюда."""
    from app.agent.scenario import scenarios, triggers

    factors = ctx.scored.matched_factors
    return {"source_id": "calc:scenario", "scenarios": scenarios(ctx.profile, factors),
            "what_could_change": [t["text"] for t in triggers(ctx.profile, factors, ctx.event.classification.effective_dates)]}


TOOLS: dict[str, tuple[Callable[..., dict], str]] = {
    "get_company_profile": (t_get_company_profile, "Профиль компании и её чувствительность к факторам"),
    "get_event": (t_get_event, "Факты, прогнозы, числа и источники события (классификация)"),
    "get_event_brief": (t_get_event_brief, "Общий разбор события: факты, каналы влияния, сверка с позицией банка"),
    "get_bank_theses": (t_get_bank_theses, "Тезисы позиции Альфа-Банка по факторам (args: factors)"),
    "get_bank_forecast": (t_get_bank_forecast, "Числовые прогнозы Альфа-Банка и их пересмотры (args: factors | indicator)"),
    "search_bank_analytics": (t_search_bank_analytics, "Поиск по аналитике Альфа-Банка (args: query)"),
    "search_related_news": (t_search_related_news, "Связанные события из базы новостей"),
    "get_counterparties": (t_get_counterparties, "Граф платежей: отрасли покупателей/поставщиков, концентрация, аффилированность"),
    "get_peer_benchmark": (t_get_peer_benchmark, "Сравнение с похожими компаниями (отрасль и масштаб): продукты, концентрация"),
    "get_playbook": (t_get_playbook, "Типовые каналы влияния, действия и продукты по факторам"),
    "check_consistency": (t_check_consistency, "Проверка противоречий между источниками и позицией банка"),
    "get_story_timeline": (t_get_story_timeline, "Хронология сюжета: связанные события до и после"),
    "get_regulation_dates": (t_get_regulation_dates, "Даты вступления в силу и сколько до них дней"),
    "scenario_calc": (t_scenario_calc, "Сценарии (база = прогноз банка, негативный, позитивный) с эффектом в ₽ и триггерами"),
}


class ToolNotAllowed(PermissionError):
    pass


def call_tool(ctx: AgentContext, name: str, args: dict | None = None) -> dict:
    if name not in TOOLS:
        raise ToolNotAllowed(f"Инструмент {name} не входит в allowlist")
    if ctx.tool_calls >= MAX_TOOL_CALLS:
        raise ToolNotAllowed("Превышен лимит вызовов инструментов на запрос")
    ctx.tool_calls += 1
    fn, _ = TOOLS[name]
    allowed_args = fn.__code__.co_varnames[1 : fn.__code__.co_argcount]
    clean_args = {k: v for k, v in (args or {}).items() if k in allowed_args}
    result = fn(ctx, **clean_args)
    key = name if name not in ctx.memory else f"{name}#{ctx.tool_calls}"
    ctx.memory[key] = result
    return result
