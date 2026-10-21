"""Детерминированные черновик и критик (работают без LLM).

Используются: (1) когда LLM недоступна (mock-режим, бюджет); (2) критик-правила работают ВСЕГДА
перед LLM-критиком — это дёшево и ловит главные ошибки (нет источника, выдуманные числа).
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime

from app.agent.schemas import Action, Claim, Consequences, Insight, ProductOffer, QualityCheck, Scenario
from app.agent.tools import AgentContext
from app.config import load_yaml
from app.knowledge.bank import get_knowledge

DIR_TEXT = {"up": "рост", "down": "снижение", "stable": "без изменений", "unknown": "изменение"}
EFFECT_RU = {"risk": "Риск", "opportunity": "Возможность", "mixed": "Риски и возможности", "watch": "Важно отслеживать"}
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _num(x: str) -> str:
    """«14,0» и «14» — одно число: нормализуем запятую и хвостовые нули."""
    x = x.replace(",", ".")
    return x.rstrip("0").rstrip(".") if "." in x else x
NO_BANK_VIEW = ("В представленных аналитических материалах Альфа-Банка нет прямой оценки этого события — "
                "выводы ниже сформированы сервисом и требуют проверки экспертом банка.")


# Главный числовой прогноз банка для фактора (показатель, период)
HEADLINE_FORECAST = {
    "key_rate": ("key_rate_end", "2026"), "inflation": ("inflation_dec", "2026"), "fx_rub": ("usd_rub", "2026"),
    "china_trade": ("cny_rub", "2026"), "labor_market": ("wages_nominal", "2026"), "consumer_demand": ("retail", "2026"),
    "credit_conditions": ("corp_loans", "2026"), "budget_policy": ("budget_balance", "2026"), "commodity_oil": ("urals", "2026"),
    "construction": ("investment", "2026"), "sanctions_trade": ("import", "2026"), "bond_market": ("key_rate_avg", "2027"),
    "real_estate": ("mortgage", "2026"), "auto_market": ("retail_loans", "2026"),
}


def headline_forecast(factor: str) -> dict | None:
    if factor not in HEADLINE_FORECAST:
        return None
    return get_knowledge().forecast(*HEADLINE_FORECAST[factor]) or None


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


def upcoming_date(dates: list[str]) -> tuple[date, int] | None:
    """Ближайшая будущая дата вступления в силу и сколько до неё дней."""
    today = datetime.now(UTC).date()
    for d in sorted(dates):
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        if dd >= today:
            return dd, (dd - today).days
    return None


def due_text(days: int) -> str:
    if days == 0:
        return "сегодня"
    n10, n100 = days % 10, days % 100
    word = "день" if n10 == 1 and n100 != 11 else "дня" if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else "дней"
    return f"через {days} {word}"


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
    due = upcoming_date(cls.effective_dates)
    if due:
        what.append(Claim(text=f"По данным источника изменение вступает в силу {due[0]:%d.%m.%Y} — {due_text(due[1])}.",
                          kind="ai_inference", sources=news_src[:1], confidence="high"))

    # 02 Почему важно
    # Причины, опирающиеся на конкретные признаки компании; базовый макрофон — только если других нет
    all_r = [r for r in scored.reasons if r["factor"] != "country"]
    reasons = ([r for r in all_r if r.get("specific")] or all_r)[:3]
    why = []
    for r in reasons:
        basis = [b for b in r.get("basis", []) if b != "baseline"]
        if r["factor"] in ("industry", "region"):  # прямое совпадение отрасли/региона — объяснение уже готово
            why.append(Claim(text=f"{r['why']}.", kind="ai_inference", sources=basis + news_src[:1], confidence="high"))
            continue
        if not basis:  # только макрофон — честно помечаем низкую уверенность
            why.append(Claim(text=f"Прямой связи с признаками компании нет: событие влияет на общий макрофон — «{r['factor_title']}».",
                             kind="ai_inference", sources=["profile:OKVED"] + news_src[:1], confidence="low"))
            continue
        dir_txt = DIR_TEXT.get(r.get("direction", "unknown"), "")
        why.append(Claim(text=f"{r['why']}. Событие затрагивает фактор «{r['factor_title']}» ({dir_txt}).",
                         kind="ai_inference", sources=basis + news_src[:1], confidence="medium"))
    # Эффект второго порядка: событие доходит до компании через отрасль её контрагентов
    indirect = next(((r, ind) for r in reasons for ind in r.get("indirect", [])), None)
    if indirect:
        r, ind = indirect
        why.append(Claim(text=f"{ind['why']} (фактор «{r['factor_title']}»).", kind="ai_inference",
                         sources=[ind["basis"], *news_src[:1]], confidence="medium"))
    for r in scored.reasons:
        if r["factor"] == "country":
            why.append(Claim(text=r["why"], kind="ai_inference", sources=r["basis"] + news_src[:1], confidence="high"))

    # 05 Позиция банка
    query = f"{ev.title} {cls.summary}"
    order = [r["factor"] for r in reasons] + [f for f in scored.matched_factors if f not in {r["factor"] for r in reasons}]
    theses: list[dict] = []
    for f in order or [h.factor for h in cls.factors]:  # по 1–2 тезиса на главные каналы влияния
        for t in kb.theses_for([f], query=query)[: (2 if not theses else 1)]:
            if t not in theses and len(theses) < 3:
                theses.append(t)
    if not theses:
        theses = kb.theses_for([h.factor for h in cls.factors], query=query)[:2]
    bank_ids = {t["factor"]: f"bank:{t['id']}" for t in theses}
    bank_view = [Claim(text=t["thesis"], kind="bank_view", sources=[f"bank:{t['id']}"], confidence="high") for t in theses]
    # Числовой прогноз банка по главному фактору — с историей пересмотров («банк повысил прогноз с … до …»)
    for f in order[:2]:
        fc = headline_forecast(f)
        if fc:
            bank_view.append(Claim(text=fc["text"], kind="bank_view", sources=[fc["ref"]], confidence="high"))
            bank_ids.setdefault(f, fc["ref"])
            break
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

    if due and len(actions) < 5:
        actions.append(Action(text=f"Заложить в план подготовку к {due[0]:%d.%m.%Y}: оценить, какие процессы, договоры и "
                                   f"расчёты затронет изменение, и назначить ответственного",
                              action_type="monitor", kind="ai_inference", sources=news_src[:1],
                              due=f"до {due[0]:%d.%m.%Y}, вступление в силу"))

    # Главный вывод — по главному фактору с плейбуком (совпадение отрасли/региона само по себе не вывод)
    top = next((r for r in reasons if r["factor"] in pb), reasons[0] if reasons else None)
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

    # Сценарии и «что может изменить вывод» — числа считает калькулятор, база — прогноз банка
    from app.agent.scenario import money, scenarios, triggers

    factors = [r["factor"] for r in reasons] + scored.matched_factors
    scen = [Scenario(**s) for s in scenarios(ctx.profile, factors)]
    change = [Claim(text=t["text"], kind="ai_inference", confidence="medium",
                    sources=news_src[:1] if t["source"] in (None, "news") else [t["source"]])
              for t in triggers(ctx.profile, factors, cls.effective_dates)]
    neg = next((s for s in scen if s.name == "negative"), None)
    if neg and neg.total_rub and len(actions) < 5:
        actions.append(Action(text=f"Проверить устойчивость бюджета к негативному сценарию — {neg.assumptions}: "
                                   f"по оценке это {money(neg.total_rub)} в год; уточните суммы в калькуляторе «Что если»",
                              action_type="analysis", kind="ai_inference", sources=["calc:scenario"]))

    from app.data.sensitivity import role_titles

    owner = role_titles().get(ctx.role or "", "") or "Финансовый директор"
    for a in actions:
        a.owner = a.owner or owner
        a.due = a.due or "в течение месяца"

    return Insight(
        event_id=ev.id, company_uid=ctx.profile.uid, role=ctx.role, scenarios=scen, what_could_change=change,
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
    out += [("what_could_change", c) for c in ins.what_could_change]
    return out


def valid_source(sid: str, ctx: AgentContext) -> bool:
    kind, _, ref = sid.partition(":")
    if kind == "news":
        return any(n.id == ref for n in ctx.event.news)
    if kind == "bank":
        kb = get_knowledge()
        return any(c.id == ref for c in kb.chunks)
    return kind in ("profile", "graph", "peers") or sid == "calc:scenario"


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
    # в разделе может быть и сравнение «новость vs банк» с пометкой «вывод сервиса» — это честно помеченный вывод;
    # ошибка — только «позиция банка» без ссылки на материалы банка или раздел без единого тезиса банка
    bank_claims = [c for c in ins.bank_view if c.kind == "bank_view"]
    check("Позиция банка подкреплена материалами", bool(ins.bank_view) and (no_view or (
        bool(bank_claims) and all(any(s.startswith("bank:") for s in c.sources) for c in bank_claims))),
        "нет ссылок на аналитику банка или вывод модели выдан за позицию банка")
    check("Персонализация (не пересказ)",
          any(any(s.startswith(("profile:", "graph:", "peers:")) for s in c.sources) for c in ins.why_it_matters),
          "раздел «почему важно» не опирается на признаки компании")
    corpus = " ".join(n.title + " " + n.text for n in ctx.event.news)
    corpus_nums = {_num(x) for x in NUM_RE.findall(corpus)}
    halluc = []
    for _, c in all_claims(ins):
        if c.kind == "fact":
            nums = {_num(x) for x in NUM_RE.findall(c.text)}
            if nums - corpus_nums:
                halluc.append(c.text[:60])
    check("Числа в фактах есть в источниках", not halluc, f"не подтверждено: {'; '.join(halluc[:3])}")
    leaked = [c.text[:40] for _, c in all_claims(ins) if re.search(r"ignore (all )?previous|system prompt|системн\w* промпт", c.text, re.I)]
    check("Нет следов prompt injection", not leaked, "; ".join(leaked))
    return checks, issues


def repair_rules(ins: Insight, ctx: AgentContext) -> Insight:
    """Детерминированная правка: убираем неподтверждённое, а не дописываем."""
    corpus = " ".join(n.title + " " + n.text for n in ctx.event.news)
    corpus_nums = {_num(x) for x in NUM_RE.findall(corpus)}

    def ok(c: Claim) -> bool:
        if not c.sources or not all(valid_source(s, ctx) for s in c.sources):
            c.sources = [s for s in c.sources if valid_source(s, ctx)]
            if not c.sources:
                return False
        if c.kind == "fact" and ({_num(x) for x in NUM_RE.findall(c.text)} - corpus_nums):
            c.kind, c.confidence = "assumption", "low"
        return not re.search(r"ignore (all )?previous|system prompt", c.text, re.I)

    ins.what_happened = [c for c in ins.what_happened if ok(c)]
    ins.why_it_matters = [c for c in ins.why_it_matters if ok(c)]
    ins.consequences.risks = [c for c in ins.consequences.risks if ok(c)]
    ins.consequences.opportunities = [c for c in ins.consequences.opportunities if ok(c)]
    ins.consequences.drivers = [c for c in ins.consequences.drivers if ok(c)]
    ins.actions = [c for c in ins.actions if ok(c)]
    ins.bank_view = [c for c in ins.bank_view if ok(c) and any(s.startswith("bank:") for s in c.sources)]
    ins.what_could_change = [c for c in ins.what_could_change if ok(c)]
    if not ins.bank_view:
        ins.bank_view = [Claim(text=NO_BANK_VIEW, kind="ai_inference", sources=[f"news:{ctx.event.news[0].id}"], confidence="low")]
    if not ok(ins.takeaway):
        ins.takeaway.sources = [f"news:{ctx.event.news[0].id}"]
    return fix_headline(ins, ctx)


def fix_headline(ins: Insight, ctx: AgentContext) -> Insight:
    """Заголовок — что случилось, главный вывод — что это значит; если модель их слила, заголовком становится событие."""
    if _overlap(ins.headline, ins.takeaway.text) > 0.6:
        ins.headline = ctx.event.title
    return ins


def _overlap(a: str, b: str) -> float:
    """Доля слов короткого текста, которые есть в другом (слова от 4 букв, без регистра)."""
    wa, wb = ({w[:6] for w in re.findall(r"[а-яёa-z0-9]{4,}", t.lower())} for t in (a, b))
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0
