"""Агент-интерпретатор: план → инструменты → черновик → самопроверка → правка → итог.

Агентная логика (критерий 11):
* план строит LLM (или правило), исполняет оркестратор с allowlist инструментов;
* все результаты инструментов складываются в общий контекст (ctx.memory);
* самопроверка: правила (всегда) + LLM-критик; при проблемах — повторный поиск/правка (до 2 итераций);
* при нехватке данных (нет позиции банка) агент сам расширяет поиск по аналитике.
Трассировка (критерий 12): каждый шаг пишется в trace_steps и доступен в UI.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent import prompts
from app.agent.humanize import humanize, humanize_insight, humanize_text
from app.agent.llm_schemas import CriticOut, InsightDraftOut, PlanOut
from app.agent.rules import critique_rules, draft_rules, fill_action_meta, fix_headline, repair_rules
from app.agent.schemas import Insight, SourceRef
from app.agent.tools import TOOLS, AgentContext, ToolNotAllowed, call_tool
from app.agent.trace import Tracer
from app.config import load_yaml
from app.data.models import CompanyProfile
from app.data.sensitivity import compute_exposures
from app.knowledge.bank import get_knowledge
from app.llm.client import LLMResult, LLMUnavailable, get_llm, llm_deadline
from app.news.models import Event
from app.ranking.scorer import score_event
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted
from app.storage import db

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")
MAX_ITERATIONS = 2  # черновик + одна правка (решение о правке — decide_revise)
MANDATORY = ["get_event_brief", "get_company_profile", "get_bank_theses", "scenario_calc"]
# Ограничители на один запрос: по исчерпании оставшиеся шаги идут без LLM (правила), отказ виден в трассировке.
# Остаток времени передаётся и в HTTP-таймаут вызова модели (llm_deadline), поэтому лимит не превышается «на хвосте».
AGENT_TIMEOUT_S = 60
AGENT_MAX_COST_RUB = 4.0
# Правка моделью — только если она нужна (замечания LLM-критика или нет позиции банка) и на неё хватает
# времени и денег; иначе замечания закрываются детерминированной правкой (repair_rules)
REVISE_MIN_LEFT_S = 25
CRITIC2_MIN_LEFT_S = 12
REVISE_COST_FALLBACK_RUB = 1.5
# Полный разбор с триггерами и сценариями длиннее 2200 токенов — обрезанный JSON уводил черновик на правила
DRAFT_MAX_TOKENS = 4000


def llm_allowed(ctx: AgentContext, tracer: Tracer) -> bool:
    """Можно ли ещё звать модель в этом запросе: время и стоимость считаются по уже выполненным шагам."""
    if ctx.memory.get("_limit"):
        return False
    spent = sum(s.get("cost_rub") or 0 for s in tracer.steps)
    elapsed = time.perf_counter() - ctx.memory.get("_t0", time.perf_counter())
    reason = (f"превышено время на запрос ({elapsed:.0f} с > {AGENT_TIMEOUT_S} с)" if elapsed > AGENT_TIMEOUT_S else
              f"превышена стоимость запроса ({spent:.2f} ₽ > {AGENT_MAX_COST_RUB} ₽)" if spent > AGENT_MAX_COST_RUB else None)
    if reason:
        ctx.memory["_limit"] = reason
        with tracer.step("limit", "rules", input={"elapsed_s": round(elapsed, 1), "cost_rub": round(spent, 3)}) as rec:
            rec["output"] = {"stop_llm": reason, "next": "оставшиеся шаги выполняются правилами"}
        return False
    return True


def _record_llm(rec: dict, res: LLMResult) -> None:
    rec.update(model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_rub=res.cost_rub)
    if res.cached:
        rec["cached"] = True
    if res.repaired:
        rec["repaired"] = True  # первый ответ не прошёл схему — модель исправила его сама


def _rule_plan(ctx: AgentContext) -> dict:
    steps = [{"tool": t, "args": {}, "why": "обязательный шаг"} for t in MANDATORY]
    steps.append({"tool": "search_bank_analytics", "args": {}, "why": "найти релевантные фрагменты аналитики банка"})
    if ctx.profile.industry_mix or ctx.profile.counterparties:
        steps.append({"tool": "get_counterparties", "args": {}, "why": "проверить эффекты через отрасли контрагентов"})
    steps.append({"tool": "get_peer_benchmark", "args": {}, "why": "сравнить с похожими компаниями"})
    if ctx.event.story_id:
        steps.append({"tool": "get_story_timeline", "args": {}, "why": "событие — часть сюжета, нужен контекст развития"})
    if ctx.event.classification.effective_dates:
        steps.append({"tool": "get_regulation_dates", "args": {}, "why": "есть дата вступления в силу"})
    steps += [{"tool": "get_bank_forecast", "args": {}, "why": "числовые прогнозы банка и их пересмотры"},
              {"tool": "get_playbook", "args": {}, "why": "типовые каналы влияния и действия"},
              {"tool": "search_related_news", "args": {}, "why": "контекст связанных событий"},
              {"tool": "check_consistency", "args": {}, "why": "противоречия источников и позиции банка"}]
    return {"focus": "Влияние через факторы: " + ", ".join(ctx.scored.matched_factors[:3]), "steps": steps}


def plan(ctx: AgentContext, tracer: Tracer) -> dict:
    llm = get_llm()
    use_llm = llm.enabled and llm_allowed(ctx, tracer)
    with tracer.step("plan", "llm" if use_llm else "rules", input={"factors": ctx.scored.matched_factors}) as rec:
        p = None
        if use_llm:
            tools = "\n".join(f"- {k}: {v[1]}" for k, v in TOOLS.items())
            msg = [{"role": "system", "content": prompts.PLAN_SYSTEM.format(notice=UNTRUSTED_NOTICE, tools=tools)},
                   {"role": "user", "content": json.dumps({
                       "event": {"title": sanitize_untrusted(ctx.event.title)[0],
                                 "factors": [f.model_dump() for f in ctx.event.classification.factors]},
                       "company_reasons": ctx.scored.reasons[:5]}, ensure_ascii=False)}]
            try:
                data, res = llm.complete_json("plan", msg, PlanOut, max_tokens=500)
                _record_llm(rec, res)
                p = data
                rejected = [s["tool"] for s in p["steps"] if s.get("tool") not in TOOLS]
                if rejected:
                    rec["rejected_tools"] = rejected  # allowlist: неизвестные инструменты не выполняются
                p["steps"] = [s for s in p["steps"] if s.get("tool") in TOOLS][:8]
                have = {s["tool"] for s in p["steps"]}
                p["steps"] = [{"tool": t, "args": {}, "why": "обязательный шаг"} for t in MANDATORY if t not in have] + p["steps"]
            except (LLMUnavailable, ValueError, KeyError, AttributeError) as e:
                rec["fallback"] = str(e)
                p = None
        p = p or _rule_plan(ctx)
        rec["output"] = p
    return p


def execute(ctx: AgentContext, p: dict, tracer: Tracer) -> None:
    for s in p["steps"]:
        with tracer.step(f"tool:{s['tool']}", "tool", input={"args": s.get("args"), "why": s.get("why")}) as rec:
            try:
                rec["output"] = call_tool(ctx, s["tool"], s.get("args"))
            except ToolNotAllowed as e:
                rec["output"] = {"denied": str(e)}
            res = ctx.memory.pop("_brief_llm", None)
            if res is not None:
                _record_llm(rec, res)


def _context_for_llm(ctx: AgentContext) -> str:
    mem = {k: v for k, v in ctx.memory.items() if not k.startswith("_")}
    # Двухуровневая генерация: если есть общий разбор события, полные тексты новостей не нужны — экономим токены
    text_limit = 600 if "get_event_brief" in mem else 2500
    ev = dict(mem.get("get_event", {}))
    ev["news"] = []
    for n in ctx.event.news:  # новости — как данные внутри тегов
        n_text, _ = sanitize_untrusted(n.text)
        ev["news"].append({"id": f"news:{n.id}", "source": n.source_title, "title": sanitize_untrusted(n.title)[0],
                           "text": n_text[:text_limit]})
    mem["get_event"] = ev
    for k in list(mem):
        if k.startswith("get_bank_theses"):
            for t in mem[k].get("theses", []):
                t["ref"] = f"bank:{t['id']}"
        if k.startswith("search_bank_analytics"):
            for r in mem[k].get("results", []):
                r["ref"] = f"bank:{r['id']}"
    mem["relevance_reasons"] = ctx.scored.reasons
    mem["role"] = ctx.role
    mem["today"] = datetime.now(MSK).date().isoformat()
    mem["allowed_sources"] = allowed_sources(ctx)
    mem["calc_baseline_forecasts"] = baseline_forecasts(mem)
    if extra := brief_theses(mem):  # тезисы, на которые ссылается общий разбор события, — с текстом
        mem["brief_bank_theses"] = extra
    # служебные поля — в начало: при обрезке длинного контекста теряются хвостовые данные, а не список допустимых ссылок
    head = ("allowed_sources", "today", "role", "calc_baseline_forecasts", "brief_bank_theses")
    mem = {**{k: mem[k] for k in head if k in mem}, **{k: v for k, v in mem.items() if k not in head}}
    return "<context>\n" + json.dumps(mem, ensure_ascii=False, default=str)[:24000] + "\n</context>"


def baseline_forecasts(mem: dict) -> list[dict]:
    """Прогнозы банка, на которые опирается база калькулятора: их id есть в allowed_sources — нужен и текст,
    иначе ссылку на них нельзя проверить (судьи отмечали «такого ref нет в контексте»)."""
    from app.agent.scenario import bank_baseline

    seen = {f.get("ref") for k, v in mem.items() if k.startswith("get_bank_forecast") for f in v.get("forecasts", [])}
    kb, out = get_knowledge(), []
    for ref in dict.fromkeys(r for r in bank_baseline()["refs"].values() if r):
        if ref in seen or not ref.startswith("bank:fc-"):
            continue
        indicator, _, period = ref[len("bank:fc-"):].rpartition("-")
        f = kb.forecast(indicator, period)
        if f:
            out.append({k: f.get(k) for k in ("ref", "title", "period", "value", "unit", "text", "date")})
    return out


def brief_theses(mem: dict) -> list[dict]:
    """Тезисы банка из сверки общего разбора события (bank_alignment), которых нет среди найденных агентом."""
    have = {t["id"] for k, v in mem.items() if k.startswith("get_bank_theses") for t in v.get("theses", [])}
    ids = [a.get("thesis_id") for a in (mem.get("get_event_brief") or {}).get("bank_alignment", [])]
    by_id = {t["id"]: t for t in get_knowledge().theses}
    return [{"ref": f"bank:{i}", **{k: by_id[i].get(k) for k in ("thesis", "type", "factor", "direction", "horizon", "quote")}}
            for i in dict.fromkeys(ids) if i and i not in have and i in by_id]


def allowed_sources(ctx: AgentContext) -> list[str]:
    """Полный список id, на которые можно ссылаться: модели не нужно угадывать, какие тезисы «существуют»."""
    ids = [f"news:{n.id}" for n in ctx.event.news]
    for k, v in ctx.memory.items():
        if k.startswith("get_bank_theses"):
            ids += [f"bank:{t['id']}" for t in v.get("theses", [])]
        elif k.startswith("search_bank_analytics"):
            ids += [f"bank:{r['id']}" for r in v.get("results", [])]
        elif k.startswith("get_bank_forecast"):
            ids += [f["ref"] for f in v.get("forecasts", []) if f.get("ref")]
        elif k.startswith("get_counterparties"):
            ids += [m["basis"] for m in v.get("industry_mix", [])] + [c["basis"] for c in v.get("top_counterparties", [])]
        elif k.startswith("scenario_calc"):
            ids.append("calc:scenario")
    for r in ctx.scored.reasons:
        ids += [b for b in r.get("basis", []) if b.startswith(("profile:", "graph:", "peers:"))]
    # прогнозы банка, которые подставляют правила и калькулятор сценариев, тоже допустимые ссылки
    kb = get_knowledge()
    ids += [f["ref"] for f in kb.forecasts_for(ctx.scored.matched_factors) if f.get("ref")]
    from app.agent.scenario import bank_baseline

    ids += [r for r in bank_baseline()["refs"].values() if r]
    ids += [f"profile:{p}" for p in ctx.profile.products] + [f"profile:OKVED={ctx.profile.okved[:2]}"]
    ids += [t["ref"] for t in brief_theses(ctx.memory)]
    return list(dict.fromkeys(ids))


def _parse_insight(data: dict, ctx: AgentContext, generated_by: str) -> Insight:
    base = draft_rules(ctx)  # недостающие поля берём из детерминированного черновика
    merged = base.model_dump()
    for k in ("headline", "takeaway", "what_happened", "why_it_matters", "consequences", "actions", "bank_view", "products",
              "what_could_change", "materiality", "materiality_why"):
        if data.get(k) or (k == "materiality" and data.get(k) == 0):
            merged[k] = data[k]
    merged["generated_by"] = generated_by
    return Insight.model_validate(merged)


def draft(ctx: AgentContext, tracer: Tracer) -> Insight:
    llm = get_llm()
    use_llm = llm.enabled and llm_allowed(ctx, tracer)
    with tracer.step("draft", "llm" if use_llm else "rules", input={"prompt_version": prompts.PROMPT_VERSION}) as rec:
        if use_llm:
            msg = [{"role": "system", "content": prompts.INTERPRET_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                   {"role": "user", "content": _context_for_llm(ctx)}]
            try:
                data, res = llm.complete_json("interpret", msg, InsightDraftOut, max_tokens=DRAFT_MAX_TOKENS)
                _record_llm(rec, res)
                ins = _parse_insight(data, ctx, f"llm:{res.model}")
                rec["output"] = ins.model_dump()
                return ins
            except (LLMUnavailable, ValueError) as e:
                rec["fallback"] = str(e)
            except Exception as e:  # некорректная структура ответа
                rec["fallback"] = f"invalid_llm_output: {e}"
        ins = draft_rules(ctx)
        rec["output"] = ins.model_dump()
        return ins


def critique(ins: Insight, ctx: AgentContext, tracer: Tracer, iteration: int, use_llm: bool = True) -> list[str]:
    with tracer.step(f"critique:rules#{iteration}", "rules", input={"claims": "all"}) as rec:
        checks, issues = critique_rules(ins, ctx)
        ins.quality = checks
        rec["output"] = {"checks": [c.model_dump() for c in checks], "issues": issues}
    llm = get_llm()
    if use_llm and llm.enabled and ins.generated_by.startswith("llm") and llm_allowed(ctx, tracer):
        with tracer.step(f"critique:llm#{iteration}", "llm") as rec:
            # сценарии и триггеры считает калькулятор — критик оценивает только текст модели
            draft_json = ins.model_dump_json(exclude={"scenarios", "what_could_change", "quality", "scores", "relevance",
                                                      "sources", "trace_id"})
            msg = [{"role": "system", "content": prompts.CRITIC_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                   {"role": "user", "content": _context_for_llm(ctx) + "\n<draft>\n" + draft_json + "\n</draft>"}]
            try:
                raw, res = llm.complete_json("critique", msg, None, max_tokens=1600)
                _record_llm(rec, res)
                if isinstance(raw, list):  # модель иногда возвращает только список замечаний
                    raw = {"verdict": "revise", "issues": [str(x) for x in raw]}
                data = CriticOut.model_validate(raw).model_dump()
                if data["scores"]:
                    ins.scores = {k: int(v) for k, v in data["scores"].items()}
                low = [k for k, v in ins.scores.items() if v < 4]
                if data["verdict"] == "revise" or low:
                    issues += [f"LLM-критик: {i}" for i in data["issues"]][:5]
                    if low and not data["issues"]:
                        issues.append(f"LLM-критик: низкие оценки по осям {', '.join(low)}")
                rec["output"] = data
            except (LLMUnavailable, ValueError) as e:  # pydantic ValidationError — подкласс ValueError
                rec["output"] = {"skipped": str(e)}
    return issues


def adapt(ins: Insight, ctx: AgentContext, issues: list[str], tracer: Tracer, use_llm: bool = True) -> Insight:
    """Корректировка процесса: доп. поиск при нехватке данных + правка черновика."""
    if any("аналитику банка" in i or "Позиция банка" in i for i in issues):
        titles = load_yaml("factors.yaml")["factors"]
        q = " ".join(titles[f]["title"] for f in ctx.scored.matched_factors if f in titles) or ctx.event.title
        why = "нет позиции банка — расширяем поиск"
        with tracer.step("tool:search_bank_analytics(retry)", "tool", input={"query": q, "why": why}) as rec:
            res = call_tool(ctx, "search_bank_analytics", {"query": q, "k": 3})
            ctx.memory["search_bank_analytics#retry"] = res
            rec["output"] = res
    llm = get_llm()
    if use_llm and llm.enabled and ins.generated_by.startswith("llm") and llm_allowed(ctx, tracer):
        with tracer.step("revise", "llm", input={"issues": issues}) as rec:
            msg = [{"role": "system", "content": prompts.REVISE_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                   {"role": "user", "content": _context_for_llm(ctx) + "\n<draft>\n" + ins.model_dump_json()
                    + "\n</draft>\n<issues>\n" + "\n".join(issues) + "\n</issues>"}]
            try:
                data, res = llm.complete_json("revise", msg, InsightDraftOut, max_tokens=DRAFT_MAX_TOKENS)
                _record_llm(rec, res)
                scores = ins.scores
                ins = _parse_insight(data, ctx, f"llm:{res.model}")
                ins.scores = scores
                rec["output"] = ins.model_dump()
            except Exception as e:
                rec["output"] = {"fallback": str(e)}
    elif not ins.generated_by.startswith("llm") and any("аналитику банка" in i or "Позиция банка" in i for i in issues):
        ins = draft_rules(ctx)  # черновик правил пересобираем с найденными фрагментами аналитики
    with tracer.step("repair:rules", "rules") as rec:
        ins = repair_rules(ins, ctx)
        rec["output"] = {"removed_unsupported": True}
    return ins


def build_sources(ins: Insight, ctx: AgentContext) -> list[SourceRef]:
    from app.agent.rules import all_claims

    kb = get_knowledge()
    ids = []
    for _, c in all_claims(ins):
        for s in c.sources:
            if s not in ids:
                ids.append(s)
    refs = []
    for sid in ids:
        kind, _, ref = sid.partition(":")
        if kind == "news":
            n = next((n for n in ctx.event.news if n.id == ref), None)
            if n:
                refs.append(SourceRef(id=sid, type="news", title=n.title, url=n.url, date=n.published_at,
                                      quote=(n.source_title or n.source)))
        elif kind == "bank":
            th = next((t for t in kb.theses if t["id"] == ref), None)
            chunk = next((c for c in kb.chunks if c.id == ref), None)
            doc = kb.doc(th["doc_id"] if th else chunk.doc_id if chunk else "")
            refs.append(SourceRef(id=sid, type="bank", title=doc.get("title", ref), date=doc.get("date"),
                                  page=(th or {}).get("page") or (chunk.page if chunk else None),
                                  quote=(th or {}).get("quote") or (chunk.text[:200] if chunk else None),
                                  stale=bool((th or {}).get("stale"))))
        elif sid == "calc:scenario":
            refs.append(SourceRef(id=sid, type="calc", title="Калькулятор сценариев: формулы и допущения в блоке «Что если»"))
        elif kind in ("profile", "graph", "peers"):
            refs.append(SourceRef(id=sid, type=kind, title=_profile_label(sid, ctx.profile)))
    return refs


def _profile_label(sid: str, p: CompanyProfile) -> str:
    _, _, ref = sid.partition(":")
    labels = {"CL_PRODUCT_LOA_MARK": "Кредитные продукты", "CL_PRODUCT_DEP_MARK": "Депозиты", "CL_PRODUCT_LEA_MARK": "Лизинг",
              "CL_PRODUCT_ZPP_MARK": "Зарплатный проект", "CL_PRODUCT_GAR_MARK": "Банковские гарантии",
              "FACTORING_MARK": "Факторинг", "FTS_FLAG": "ВЭД (данные ФТС)", "CL_PRODUCT_RKO_MARK": "РКО"}
    if ref.startswith("OKVED"):
        return f"ОКВЭД {p.okved}: {p.okved_name}"
    if ref.startswith("FTS_COUNTRY"):
        return f"Страны ВЭД: {', '.join(p.fts_countries)}"
    if sid.startswith("graph:"):
        _, direction, okved = sid.split(":", 2)
        who = "покупателей" if direction == "in" else "поставщиков"
        mix = next((m for m in p.industry_mix if m["direction"] == direction and m["okved2"] == okved[:2]), None)
        if mix:
            return f"Граф платежей: {mix['share']:.0%} платежей {who} — отрасль «{mix['name']}»"
        cp = next((c for c in p.counterparties if c.direction == direction and c.okved == okved), None)
        return f"Граф платежей: {cp.okved_name} ({cp.share:.0%} потока {who})" if cp else f"Граф платежей: отрасль {okved}"
    if sid.startswith("peers:"):
        from app.data.peers import PRODUCT_TITLES, peer_stats

        st = peer_stats(p.peer_key) or {}
        if ref not in PRODUCT_TITLES:
            return f"Похожие компании ({st.get('n', '?')}): " + {"buyer_concentration_vs_peers": "концентрация покупателей в сравнении",
                                                               "median_revenue": "медианная выручка"}.get(ref, "сравнение с группой")
        share = (st.get("products") or {}).get(ref, 0)
        return f"Похожие компании ({st.get('n', '?')}): {PRODUCT_TITLES.get(ref, ref)} есть у {share:.0%}"
    from app.agent.humanize import _FIELDS

    return labels.get(ref) or ("Профиль компании: " + _FIELDS[ref] if ref in _FIELDS else "Профиль компании")


def decide_revise(ins: Insight, ctx: AgentContext, issues: list[str], tracer: Tracer) -> bool:
    """Нужна ли правка моделью и хватает ли на неё времени и денег. Решение и причина — в трассировке."""
    # суммы не из калькулятора и пересказ заголовка правилами не исправить — нужна правка моделью
    llm_issues = [i for i in issues if i.startswith(("LLM-критик", "Суммы — только из калькулятора", "Главный вывод не повторяет"))
                  or (i.startswith("Числа позиции банка") and "сверьте" in i)]
    no_bank = any("аналитику банка" in i or "Позиция банка" in i for i in issues)
    elapsed = time.perf_counter() - ctx.memory["_t0"]
    spent = sum(s.get("cost_rub") or 0 for s in tracer.steps)
    est = next((s.get("cost_rub") for s in tracer.steps if s.get("name") == "draft" and s.get("cost_rub")), None)
    est = est or REVISE_COST_FALLBACK_RUB
    if not ins.generated_by.startswith("llm") or not get_llm().enabled:
        verdict, why = False, "черновик построен правилами — правка правилами"
    elif not (llm_issues or no_bank):
        verdict, why = False, "замечания только формальные — их закрывает детерминированная правка"
    elif not no_bank and ins.scores and min(ins.scores.values()) >= 4:
        # критик почти всегда что-то советует; правка моделью окупается, только если есть заметный пробел (оценка < 4)
        verdict, why = False, f"оценки критика не ниже 4 (минимум {min(ins.scores.values())}) — правка моделью не окупится"
    elif AGENT_TIMEOUT_S - elapsed < REVISE_MIN_LEFT_S:
        verdict, why = False, f"на правку моделью не хватает времени ({AGENT_TIMEOUT_S - elapsed:.0f} с из {REVISE_MIN_LEFT_S} с)"
    elif spent + est > AGENT_MAX_COST_RUB:
        verdict, why = False, f"правка выйдет за лимит стоимости ({spent:.2f} + ~{est:.2f} ₽ > {AGENT_MAX_COST_RUB} ₽)"
    else:
        verdict, why = True, f"замечаний критика: {len(llm_issues)}" + (", нет позиции банка" if no_bank else "")
    with tracer.step("decide:revise", "rules", input={"issues": len(issues), "elapsed_s": round(elapsed, 1),
                                                       "cost_rub": round(spent, 3)}) as rec:
        rec["output"] = {"revise_with_llm": verdict, "why": why}
    return verdict


def emit_draft(ins: Insight, ctx: AgentContext, tracer: Tracer) -> None:
    """Черновик модели — пользователю сразу (≈20 с), пока критик и правка работают: разбор читается, а не ждётся.

    Отдаётся копия с проверками правилами и источниками; итоговый разбор после критика приходит событием done.
    """
    try:
        d = fix_headline(ins.model_copy(deep=True), ctx)
        d.quality, _ = critique_rules(d, ctx)
        d.sources = build_sources(d, ctx)
        d.trace_id = tracer.trace_id
        d.relevance = {"score": ctx.scored.score, "reasons": ctx.scored.reasons}
        tracer._emit({"type": "draft", "insight": humanize_insight(ensure_scenarios(d)).model_dump()})
    except Exception:  # черновик — удобство, он не должен ломать разбор
        log.exception("Не удалось отдать черновик разбора")


def run_agent(event: Event, profile: CompanyProfile, role: str | None = None, listener=None) -> Insight:
    tracer = Tracer(listener=listener)
    exposures = compute_exposures(profile, role)
    scored = score_event(event, profile, exposures)
    ctx = AgentContext(event=event, profile=profile, role=role, exposures=exposures, scored=scored)
    ctx.memory["_t0"] = time.perf_counter()
    with tracer.step("relevance", "rules", input={"company": profile.uid, "role": role}) as rec:
        rec["output"] = {"score": scored.score, "effect": scored.effect, "reasons": scored.reasons}
    # Прогрессивная выдача: главный вывод по скорингу и плейбуку — сразу, полный разбор догружается
    if listener:
        with tracer.step("preview", "rules") as rec:
            pv = draft_rules(ctx)
            rec["output"] = {"headline": pv.headline, "takeaway": pv.takeaway.text}
        tracer._emit({"type": "preview", "insight": {"headline": humanize_text(pv.headline), "effect": pv.effect,
                                                      "takeaway": humanize(pv.takeaway.model_dump()),
                                                      "scenarios": [s.model_dump() for s in pv.scenarios]}})

    with llm_deadline(AGENT_TIMEOUT_S):
        # разбор события (общий для всех клиентов, самый долгий инструмент) строится параллельно с планом
        pool = ThreadPoolExecutor(max_workers=1)
        if get_llm().enabled:
            from app.agent.event_brief import get_event_brief

            ctx.memory["_brief_future"] = pool.submit(contextvars.copy_context().run, get_event_brief, event)
        p = plan(ctx, tracer)
        execute(ctx, p, tracer)
        pool.shutdown(wait=False)
        _allowed = allowed_sources(ctx)
        ctx.memory["_allowed_bank"] = {s for s in _allowed if s.startswith("bank:")}
        ctx.memory["_allowed_all"] = set(_allowed)
        ins = fill_action_meta(draft(ctx, tracer), ctx)
        if listener and ins.generated_by.startswith("llm"):
            emit_draft(ins, ctx, tracer)
        iterations = 1
        issues = critique(ins, ctx, tracer, 1)
        if issues:
            revise = decide_revise(ins, ctx, issues, tracer)
            ins = adapt(ins, ctx, issues, tracer, use_llm=revise)
            iterations = 2
            left = AGENT_TIMEOUT_S - (time.perf_counter() - ctx.memory["_t0"])
            critique(ins, ctx, tracer, 2, use_llm=revise and left >= CRITIC2_MIN_LEFT_S)
    if any(not c.passed for c in ins.quality):
        ins = repair_rules(ins, ctx)
        ins.quality, _ = critique_rules(ins, ctx)

    ins.iterations = iterations
    ins = fix_headline(ins, ctx)
    ins.sources = build_sources(ins, ctx)
    ins.trace_id = tracer.trace_id
    ins.relevance = {"score": scored.score, "reasons": scored.reasons, "focus": p.get("focus")}
    if issues_ := (ctx.memory.get("check_consistency") or {}).get("issues"):
        ins.relevance["consistency_issues"] = issues_
    if ctx.memory.get("_limit"):
        ins.relevance["limit"] = ctx.memory["_limit"]
    with tracer.step("finalize", "rules") as rec:
        rec["output"] = {"sources": len(ins.sources), "quality_passed": all(c.passed for c in ins.quality)}
    return ins


def insight_key(event_id: str, company_uid: str, role: str | None) -> str:
    return f"{event_id}|{company_uid}|{role or '-'}"


def cached_insight(event_id: str, company_uid: str, role: str | None) -> Insight | None:
    row = db.query_one("SELECT payload FROM insights WHERE key=?", (insight_key(event_id, company_uid, role),))
    return humanize_insight(ensure_scenarios(Insight.model_validate_json(row["payload"]))) if row else None


def ensure_scenarios(ins: Insight) -> Insight:
    """Разборы, построенные до появления сценариев (кэш, снапшот), дополняются расчётом без LLM."""
    from app.agent.scenario import CALC_VERSION

    if ins.scenarios and ins.scenarios[0].version == CALC_VERSION:
        return ins
    from app.agent.scenario import scenarios
    from app.agent.schemas import Scenario
    from app.data.profiles import get_profile

    p = get_profile(ins.company_uid)
    factors = [r["factor"] for r in ins.relevance.get("reasons", []) if "factor" in r]
    if p:
        from app.news.ingest import get_event

        ev = get_event(ins.event_id)
        dates = ev.classification.effective_dates if ev else []
        ins.scenarios = [Scenario(**s) for s in scenarios(p, factors, reasons=ins.relevance.get("reasons", []),
                                                          effective_dates=dates, regions=ev.classification.regions if ev else None,
                                                          countries=ev.classification.countries if ev else None)]
    return ins


def get_or_create_insight(event: Event, profile: CompanyProfile, role: str | None, force: bool = False,
                          listener=None) -> Insight:
    key = insight_key(event.id, profile.uid, role)
    if not force and (hit := cached_insight(event.id, profile.uid, role)):
        return hit
    ins = humanize_insight(run_agent(event, profile, role, listener=listener))
    old = cached_insight(event.id, profile.uid, role) if force else None
    if old and ins.generated_by == "rules" and old.generated_by != "rules":
        return old  # модель недоступна — не затираем готовый разбор моделью детерминированным
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO insights(key, event_id, company_uid, role, payload, trace_id) VALUES (?,?,?,?,?,?)",
                     (key, event.id, profile.uid, role, ins.model_dump_json(), ins.trace_id))
    return ins
