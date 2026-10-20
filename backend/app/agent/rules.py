"""Детерминированные черновик и критик (работают без LLM).

Используются: (1) когда LLM недоступна (mock-режим, бюджет); (2) критик-правила работают ВСЕГДА
перед LLM-критиком — это дёшево и ловит главные ошибки (нет источника, выдуманные числа).
"""
from __future__ import annotations

import re

from app.agent.schemas import Action, Claim, Consequences, Insight, ProductOffer, QualityCheck
from app.agent.tools import AgentContext
from app.config import load_yaml
from app.knowledge.bank import get_knowledge

DIR_TEXT = {"up": "рост", "down": "снижение", "stable": "без изменений", "unknown": "изменение"}
EFFECT_RU = {"risk": "Риск", "opportunity": "Возможность", "mixed": "Риски и возможности", "watch": "Важно отслеживать"}
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
NO_BANK_VIEW = ("В представленных аналитических материалах Альфа-Банка нет прямой оценки этого события — "
                "выводы ниже сформированы сервисом и требуют проверки экспертом банка.")


def _company_short(ctx: AgentContext) -> str:
    p = ctx.profile
    name = p.okved_name.split(",")[0].lower()
    parts = [name]
    if "CL_PRODUCT_LOA_MARK" in p.products:
        parts.append("с кредитом")
    if p.fts:
        parts.append("с ВЭД" + (f" ({', '.join(p.fts_countries[:2]).title()})" if p.fts_countries else ""))
    return ", ".join(parts)


def _products_ok(when: str | None, products: list[str]) -> bool:
    if not when:
        return True
    kind, _, feature = when.partition(":")
    return (feature in products) if kind == "has" else (feature not in products)


def draft_rules(ctx: AgentContext) -> Insight:
    ev, cls, scored = ctx.event, ctx.event.classification, ctx.scored
    pb = load_yaml("playbook.yaml")["factors"]
    kb = get_knowledge()
    news_src = [f"news:{n.id}" for n in ev.news]

    # 01 Что произошло
    what = [Claim(text=cls.summary, kind="fact", sources=news_src, confidence="high")]
    for f in cls.facts[1:3]:
        if f != cls.summary:
            what.append(Claim(text=f, kind="fact", sources=news_src[:1], confidence="high"))
    for o in cls.forecasts_or_opinions[:2]:
        what.append(Claim(text=f"Прогноз/мнение в источнике: {o}", kind="assumption", sources=news_src[:1], confidence="low"))

    # 02 Почему важно
    # Причины, опирающиеся на конкретные признаки компании; базовый макрофон — только если других нет
    all_r = [r for r in scored.reasons if r["factor"] != "country"]
    reasons = ([r for r in all_r if r.get("specific")] or all_r)[:3]
    why = []
    for r in reasons:
        basis = [b for b in r.get("basis", []) if b != "baseline"]
        if not basis:  # только макрофон — честно помечаем низкую уверенность
            why.append(Claim(text=f"Прямой связи с признаками компании нет: событие влияет на общий макрофон — «{r['factor_title']}».",
                             kind="ai_inference", sources=["profile:OKVED"] + news_src[:1], confidence="low"))
            continue
        dir_txt = DIR_TEXT.get(r.get("direction", "unknown"), "")
        why.append(Claim(text=f"{r['why']}. Событие затрагивает фактор «{r['factor_title']}» ({dir_txt}).",
                         kind="ai_inference", sources=basis + news_src[:1], confidence="medium"))
    for r in scored.reasons:
        if r["factor"] == "country":
            why.append(Claim(text=r["why"], kind="ai_inference", sources=r["basis"] + news_src[:1], confidence="high"))

    # 05 Позиция банка
    theses = kb.theses_for(scored.matched_factors)[:3] or kb.theses_for([f.factor for f in cls.factors])[:2]
    bank_ids = {t["factor"]: f"bank:{t['id']}" for t in theses}
    bank_view = [Claim(text=t["thesis"], kind="bank_view", sources=[f"bank:{t['id']}"], confidence="high") for t in theses]
    for res in (ctx.memory.get("search_bank_analytics#retry") or {}).get("results", [])[:1]:
        bank_view.append(Claim(text=res["text"], kind="bank_view", sources=[f"bank:{res['id']}"], confidence="medium"))
    if not bank_view:  # честно говорим, что позиции банка нет, а не выдумываем её
        bank_view.append(Claim(text=NO_BANK_VIEW, kind="ai_inference", sources=news_src[:1], confidence="low"))

    # 03 Последствия
    cons = Consequences()
    for r in reasons:
        f, eff = r["factor"], r["effect"]
        play = pb.get(f)
        if not play:
            continue
        src = [b for b in r.get("basis", []) if b != "baseline"][:2] + news_src[:1] + ([bank_ids[f]] if f in bank_ids else [])
        if eff in ("risk", "mixed"):
            cons.risks.append(Claim(text=play["risk"], kind="ai_inference", sources=src))
        if eff in ("opportunity", "mixed"):
            cons.opportunities.append(Claim(text=play["opportunity"], kind="ai_inference", sources=src))
        if eff == "watch":
            exp = ctx.exposures.get(f)
            target = cons.risks if (exp and exp.sign < 0) else cons.opportunities if (exp and exp.sign > 0) else cons.drivers
            target.append(Claim(text=play["stable"], kind="ai_inference", sources=src))
        for d in play.get("drivers", [])[:2]:
            if all(d != c.text for c in cons.drivers):
                cons.drivers.append(Claim(text=d, kind="ai_inference", sources=news_src[:1], confidence="medium"))
    if not (cons.risks or cons.opportunities):
        cons.drivers.insert(0, Claim(text="Прямого влияния на компанию не выявлено; событие важно как часть макрофона.",
                                     kind="ai_inference", sources=news_src[:1], confidence="low"))

    # 04 Действия и продукты
    actions: list[Action] = []
    products: list[ProductOffer] = []
    for r in reasons:
        play = pb.get(r["factor"]) or {}
        for a in play.get("actions", []):
            if len(actions) < 4 and all(a["text"] != x.text for x in actions):
                actions.append(Action(text=a["text"], action_type=a.get("type", "analysis"), kind="ai_inference",
                                      sources=[b for b in r.get("basis", []) if b != "baseline"][:1] + news_src[:1]))
        for p in play.get("products", []):
            if len(products) < 2 and _products_ok(p.get("when"), ctx.profile.products) and all(p["product"] != x.product for x in products):
                products.append(ProductOffer(product=p["product"], why=p["why"]))

    # Главный вывод
    top = reasons[0] if reasons else None
    effect = scored.effect
    if top and pb.get(top["factor"]):
        play = pb[top["factor"]]
        key = {"risk": "risk", "opportunity": "opportunity", "mixed": "risk"}.get(effect, "stable")
        text = f"Для компании ({_company_short(ctx)}) это {EFFECT_RU[effect].lower()}: {play[key]}"
        bank_ref = [bank_ids[top["factor"]]] if top["factor"] in bank_ids else []
        t_src = [b for b in top.get("basis", []) if b != "baseline"][:2] + news_src[:1] + bank_ref
    else:
        text, t_src = f"Событие слабо связано с бизнесом компании ({_company_short(ctx)}).", news_src[:1]
    takeaway = Claim(text=text, kind="ai_inference", sources=t_src, confidence="medium")

    return Insight(
        event_id=ev.id, company_uid=ctx.profile.uid, role=ctx.role,
        headline=f"{EFFECT_RU[effect]}: {ev.title}", takeaway=takeaway,
        what_happened=what, why_it_matters=why, consequences=cons, actions=actions, bank_view=bank_view,
        sources=[], products=products, effect=effect, generated_by="rules", trace_id="",
        relevance={"score": scored.score, "reasons": scored.reasons}, data_gaps=ctx.profile.data_gaps,
    )


# ------------------------------------------------------------------ критик
def all_claims(ins: Insight) -> list[tuple[str, Claim]]:
    out = [("takeaway", ins.takeaway)]
    out += [("what_happened", c) for c in ins.what_happened]
    out += [("why_it_matters", c) for c in ins.why_it_matters]
    out += [("risks", c) for c in ins.consequences.risks]
    out += [("opportunities", c) for c in ins.consequences.opportunities]
    out += [("drivers", c) for c in ins.consequences.drivers]
    out += [("actions", c) for c in ins.actions]
    out += [("bank_view", c) for c in ins.bank_view]
    return out


def valid_source(sid: str, ctx: AgentContext) -> bool:
    kind, _, ref = sid.partition(":")
    if kind == "news":
        return any(n.id == ref for n in ctx.event.news)
    if kind == "bank":
        kb = get_knowledge()
        return any(c.id == ref for c in kb.chunks)
    return kind in ("profile", "graph")


def critique_rules(ins: Insight, ctx: AgentContext) -> tuple[list[QualityCheck], list[str]]:
    checks: list[QualityCheck] = []
    issues: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(QualityCheck(name=name, passed=ok, detail="" if ok else detail))
        if not ok:
            issues.append(f"{name}: {detail}")

    check("Ответ на 6 вопросов", all([ins.what_happened, ins.why_it_matters,
          ins.consequences.risks or ins.consequences.opportunities or ins.consequences.drivers, ins.actions, ins.bank_view]),
          "не все разделы заполнены")
    no_src = [s for s, c in all_claims(ins) if not c.sources]
    check("У каждого вывода есть источник", not no_src, f"без источника: {', '.join(no_src)}")
    bad = sorted({sid for _, c in all_claims(ins) for sid in c.sources if not valid_source(sid, ctx)})
    check("Ссылки на источники корректны", not bad, f"неизвестные источники: {', '.join(bad[:5])}")
    no_view = len(ins.bank_view) == 1 and ins.bank_view[0].text == NO_BANK_VIEW
    check("Позиция банка подкреплена материалами", bool(ins.bank_view) and (no_view or all(
        c.kind == "bank_view" and any(s.startswith("bank:") for s in c.sources) for c in ins.bank_view)),
        "нет ссылок на аналитику банка или вывод модели выдан за позицию банка")
    check("Персонализация (не пересказ)", any(any(s.startswith(("profile:", "graph:")) for s in c.sources) for c in ins.why_it_matters),
          "раздел «почему важно» не опирается на признаки компании")
    corpus = " ".join(n.title + " " + n.text for n in ctx.event.news)
    corpus_nums = {x.replace(",", ".") for x in NUM_RE.findall(corpus)}
    halluc = []
    for _, c in all_claims(ins):
        if c.kind == "fact":
            nums = {x.replace(",", ".") for x in NUM_RE.findall(c.text)}
            if nums - corpus_nums:
                halluc.append(c.text[:60])
    check("Числа в фактах есть в источниках", not halluc, f"не подтверждено: {'; '.join(halluc[:3])}")
    leaked = [c.text[:40] for _, c in all_claims(ins) if re.search(r"ignore (all )?previous|system prompt|системн\w* промпт", c.text, re.I)]
    check("Нет следов prompt injection", not leaked, "; ".join(leaked))
    return checks, issues


def repair_rules(ins: Insight, ctx: AgentContext) -> Insight:
    """Детерминированная правка: убираем неподтверждённое, а не дописываем."""
    corpus = " ".join(n.title + " " + n.text for n in ctx.event.news)
    corpus_nums = {x.replace(",", ".") for x in NUM_RE.findall(corpus)}

    def ok(c: Claim) -> bool:
        if not c.sources or not all(valid_source(s, ctx) for s in c.sources):
            c.sources = [s for s in c.sources if valid_source(s, ctx)]
            if not c.sources:
                return False
        if c.kind == "fact" and ({x.replace(",", ".") for x in NUM_RE.findall(c.text)} - corpus_nums):
            c.kind, c.confidence = "assumption", "low"
        return not re.search(r"ignore (all )?previous|system prompt", c.text, re.I)

    ins.what_happened = [c for c in ins.what_happened if ok(c)]
    ins.why_it_matters = [c for c in ins.why_it_matters if ok(c)]
    ins.consequences.risks = [c for c in ins.consequences.risks if ok(c)]
    ins.consequences.opportunities = [c for c in ins.consequences.opportunities if ok(c)]
    ins.consequences.drivers = [c for c in ins.consequences.drivers if ok(c)]
    ins.actions = [c for c in ins.actions if ok(c)]
    ins.bank_view = [c for c in ins.bank_view if ok(c) and any(s.startswith("bank:") for s in c.sources)]
    if not ins.bank_view:
        ins.bank_view = [Claim(text=NO_BANK_VIEW, kind="ai_inference", sources=[f"news:{ctx.event.news[0].id}"], confidence="low")]
    if not ok(ins.takeaway):
        ins.takeaway.sources = [f"news:{ctx.event.news[0].id}"]
    return ins
