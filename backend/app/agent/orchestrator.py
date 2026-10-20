"""Агент-интерпретатор: план → инструменты → черновик → самопроверка → правка → итог.

Агентная логика (критерий 11):
* план строит LLM (или правило), исполняет оркестратор с allowlist инструментов;
* все результаты инструментов складываются в общий контекст (ctx.memory);
* самопроверка: правила (всегда) + LLM-критик; при проблемах — повторный поиск/правка (до 2 итераций);
* при нехватке данных (нет позиции банка) агент сам расширяет поиск по аналитике.
Трассировка (критерий 12): каждый шаг пишется в trace_steps и доступен в UI.
"""
from __future__ import annotations

import json
import logging

from app.agent import prompts
from app.agent.rules import critique_rules, draft_rules, repair_rules
from app.agent.schemas import Insight, SourceRef
from app.agent.tools import TOOLS, AgentContext, ToolNotAllowed, call_tool
from app.agent.trace import Tracer
from app.config import load_yaml
from app.data.models import CompanyProfile
from app.data.sensitivity import compute_exposures
from app.knowledge.bank import get_knowledge
from app.llm.client import LLMResult, LLMUnavailable, get_llm
from app.news.models import Event
from app.ranking.scorer import score_event
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted
from app.storage import db

log = logging.getLogger(__name__)
MAX_ITERATIONS = 2
MANDATORY = ["get_event", "get_company_profile", "get_bank_theses"]


def _record_llm(rec: dict, res: LLMResult) -> None:
    rec.update(model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_rub=res.cost_rub)


def _rule_plan(ctx: AgentContext) -> dict:
    steps = [{"tool": t, "args": {}, "why": "обязательный шаг"} for t in MANDATORY]
    steps.append({"tool": "search_bank_analytics", "args": {}, "why": "найти релевантные фрагменты аналитики банка"})
    if any(r.get("basis") and any(b.startswith("graph:") for b in r["basis"]) for r in ctx.scored.reasons) or \
            (ctx.profile.top_buyer_share or 0) > 0.3:
        steps.append({"tool": "get_counterparties", "args": {}, "why": "проверить эффекты через контрагентов"})
    steps += [{"tool": "get_playbook", "args": {}, "why": "типовые каналы влияния и действия"},
              {"tool": "search_related_news", "args": {}, "why": "контекст связанных событий"},
              {"tool": "check_consistency", "args": {}, "why": "противоречия источников и позиции банка"}]
    return {"focus": "Влияние через факторы: " + ", ".join(ctx.scored.matched_factors[:3]), "steps": steps}


def plan(ctx: AgentContext, tracer: Tracer) -> dict:
    llm = get_llm()
    with tracer.step("plan", "llm" if llm.live else "rules", input={"factors": ctx.scored.matched_factors}) as rec:
        p = None
        if llm.live:
            tools = "\n".join(f"- {k}: {v[1]}" for k, v in TOOLS.items())
            msg = [{"role": "system", "content": prompts.PLAN_SYSTEM.format(notice=UNTRUSTED_NOTICE, tools=tools)},
                   {"role": "user", "content": json.dumps({
                       "event": {"title": sanitize_untrusted(ctx.event.title)[0],
                                 "factors": [f.model_dump() for f in ctx.event.classification.factors]},
                       "company_reasons": ctx.scored.reasons[:5]}, ensure_ascii=False)}]
            try:
                res = llm.complete("plan", msg, json_mode=True, max_tokens=500)
                _record_llm(rec, res)
                p = res.json()
                p["steps"] = [s for s in p.get("steps", []) if s.get("tool") in TOOLS][:7]
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


def _context_for_llm(ctx: AgentContext) -> str:
    mem = dict(ctx.memory)
    ev = mem.get("get_event", {})
    for n in ctx.event.news:  # новости — как данные внутри тегов
        n_text, _ = sanitize_untrusted(n.text)
        ev.setdefault("news", []).append({"id": f"news:{n.id}", "source": n.source_title, "title": n.title, "text": n_text[:2500]})
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
    return "<context>\n" + json.dumps(mem, ensure_ascii=False, default=str)[:24000] + "\n</context>"


def _parse_insight(data: dict, ctx: AgentContext, generated_by: str) -> Insight:
    base = draft_rules(ctx)  # недостающие поля берём из детерминированного черновика
    merged = base.model_dump()
    for k in ("headline", "takeaway", "what_happened", "why_it_matters", "consequences", "actions", "bank_view", "products"):
        if data.get(k):
            merged[k] = data[k]
    merged["generated_by"] = generated_by
    return Insight.model_validate(merged)


def draft(ctx: AgentContext, tracer: Tracer) -> Insight:
    llm = get_llm()
    with tracer.step("draft", "llm" if llm.live else "rules", input={"prompt_version": prompts.PROMPT_VERSION}) as rec:
        if llm.live:
            msg = [{"role": "system", "content": prompts.INTERPRET_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                   {"role": "user", "content": _context_for_llm(ctx)}]
            try:
                res = llm.complete("interpret", msg, json_mode=True, max_tokens=2000)
                _record_llm(rec, res)
                ins = _parse_insight(res.json(), ctx, f"llm:{res.model}")
                rec["output"] = ins.model_dump()
                return ins
            except (LLMUnavailable, ValueError) as e:
                rec["fallback"] = str(e)
            except Exception as e:  # некорректная структура ответа
                rec["fallback"] = f"invalid_llm_output: {e}"
        ins = draft_rules(ctx)
        rec["output"] = ins.model_dump()
        return ins


def critique(ins: Insight, ctx: AgentContext, tracer: Tracer, iteration: int) -> list[str]:
    with tracer.step(f"critique:rules#{iteration}", "rules", input={"claims": "all"}) as rec:
        checks, issues = critique_rules(ins, ctx)
        ins.quality = checks
        rec["output"] = {"checks": [c.model_dump() for c in checks], "issues": issues}
    llm = get_llm()
    if llm.live and ins.generated_by.startswith("llm"):
        with tracer.step(f"critique:llm#{iteration}", "llm") as rec:
            msg = [{"role": "system", "content": prompts.CRITIC_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                   {"role": "user", "content": _context_for_llm(ctx) + "\n<draft>\n" + ins.model_dump_json() + "\n</draft>"}]
            try:
                res = llm.complete("critique", msg, json_mode=True, max_tokens=600)
                _record_llm(rec, res)
                data = res.json()
                if data.get("verdict") == "revise":
                    issues += [f"LLM-критик: {i}" for i in data.get("issues", [])][:5]
                rec["output"] = data
            except (LLMUnavailable, ValueError) as e:
                rec["output"] = {"skipped": str(e)}
    return issues


def adapt(ins: Insight, ctx: AgentContext, issues: list[str], tracer: Tracer) -> Insight:
    """Корректировка процесса: доп. поиск при нехватке данных + правка черновика."""
    if any("аналитику банка" in i or "Позиция банка" in i for i in issues):
        titles = load_yaml("factors.yaml")["factors"]
        q = " ".join(titles[f]["title"] for f in ctx.scored.matched_factors if f in titles) or ctx.event.title
        with tracer.step("tool:search_bank_analytics(retry)", "tool", input={"query": q, "why": "нет позиции банка — расширяем поиск"}) as rec:
            res = call_tool(ctx, "search_bank_analytics", {"query": q, "k": 3})
            ctx.memory["search_bank_analytics#retry"] = res
            rec["output"] = res
    llm = get_llm()
    if llm.live and ins.generated_by.startswith("llm"):
        with tracer.step("revise", "llm", input={"issues": issues}) as rec:
            msg = [{"role": "system", "content": prompts.REVISE_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                   {"role": "user", "content": _context_for_llm(ctx) + "\n<draft>\n" + ins.model_dump_json()
                    + "\n</draft>\n<issues>\n" + "\n".join(issues) + "\n</issues>"}]
            try:
                res = llm.complete("revise", msg, json_mode=True, max_tokens=2000)
                _record_llm(rec, res)
                ins = _parse_insight(res.json(), ctx, f"llm:{res.model}")
                rec["output"] = ins.model_dump()
            except Exception as e:
                rec["output"] = {"fallback": str(e)}
    elif any("аналитику банка" in i or "Позиция банка" in i for i in issues):
        ins = draft_rules(ctx)
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
        elif kind in ("profile", "graph"):
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
        cp = next((c for c in p.counterparties if c.direction == direction and c.okved == okved), None)
        who = "Покупатель" if direction == "in" else "Поставщик"
        return f"{who} из графа платежей: {cp.okved_name if cp else okved} ({cp.share:.0%} потока)" if cp else f"{who}: {okved}"
    return labels.get(ref, ref)


def run_agent(event: Event, profile: CompanyProfile, role: str | None = None) -> Insight:
    tracer = Tracer()
    exposures = compute_exposures(profile, role)
    scored = score_event(event, profile, exposures)
    ctx = AgentContext(event=event, profile=profile, role=role, exposures=exposures, scored=scored)
    with tracer.step("relevance", "rules", input={"company": profile.uid, "role": role}) as rec:
        rec["output"] = {"score": scored.score, "effect": scored.effect, "reasons": scored.reasons}

    p = plan(ctx, tracer)
    execute(ctx, p, tracer)
    ins = draft(ctx, tracer)
    iterations = 1
    for i in range(1, MAX_ITERATIONS + 1):
        issues = critique(ins, ctx, tracer, i)
        if not issues:
            break
        if i == MAX_ITERATIONS:
            break
        ins = adapt(ins, ctx, issues, tracer)
        iterations += 1
    if any(not c.passed for c in ins.quality):
        ins = repair_rules(ins, ctx)
        ins.quality, _ = critique_rules(ins, ctx)

    ins.iterations = iterations
    ins.sources = build_sources(ins, ctx)
    ins.trace_id = tracer.trace_id
    ins.relevance = {"score": scored.score, "reasons": scored.reasons, "focus": p.get("focus")}
    if issues_ := (ctx.memory.get("check_consistency") or {}).get("issues"):
        ins.relevance["consistency_issues"] = issues_
    with tracer.step("finalize", "rules") as rec:
        rec["output"] = {"sources": len(ins.sources), "quality_passed": all(c.passed for c in ins.quality)}
    return ins


def get_or_create_insight(event: Event, profile: CompanyProfile, role: str | None, force: bool = False) -> Insight:
    key = f"{event.id}|{profile.uid}|{role or '-'}"
    if not force:
        row = db.query_one("SELECT payload FROM insights WHERE key=?", (key,))
        if row:
            return Insight.model_validate_json(row["payload"])
    ins = run_agent(event, profile, role)
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO insights(key, event_id, company_uid, role, payload, trace_id) VALUES (?,?,?,?,?,?)",
                     (key, event.id, profile.uid, role, ins.model_dump_json(), ins.trace_id))
    return ins
