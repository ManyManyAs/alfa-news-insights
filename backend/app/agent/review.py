"""Проверяемость разбора (этап 1 плана улучшений): что сервис может подтвердить сам, без модели.

Считается при выдаче разбора (не хранится в кэше), поэтому работает и для разборов, собранных раньше:
- тип события и что разбор обязан ответить для такого типа (решение ЦБ, пошлина, налог, санкции, поддержка…);
- сколько независимых источников подтверждают каждый факт;
- дата материала банка и пометка «могла обновиться» для материалов старше квартала;
- цифры новости и прогноз банка рядом — без выбора, кто прав;
- что изменилось с прошлого события того же сюжета.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime

from app.agent.rules import NUM_RE, _num, all_claims, headline_forecast
from app.agent.schemas import Insight
from app.knowledge.bank import get_knowledge

BANK_FRESH_DAYS = 90  # материал старше квартала — «позиция могла обновиться» (макропрогноз банк пересматривает раз в квартал)

PCT = r"\d+(?:[.,]\d+)?\s?%"
DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b|\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|"
                     r"ноябр|декабр)\w*|\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+20\d\d|"
                     r"\bс\s+20\d\d\b|\bв\s+20\d\d\s+год", re.I)
MONEY_OR_PCT = re.compile(PCT + r"|\d+(?:[.,]\d+)?\s*(?:млрд|млн|тыс\.?)?\s*(?:₽|руб)|\d+(?:[.,]\d+)?\s*(?:п\.\s?п\.|б\.\s?п\.)", re.I)


# --------------------------------------------------------------- тип события и обязательные ответы
def _text(claims) -> str:
    return " ".join(c.text for c in claims)


def _src(claims, *prefixes: str) -> bool:
    return any(s.startswith(prefixes) for c in claims for s in c.sources)


def _has_number(claims) -> bool:
    return any(MONEY_OR_PCT.search(c.text) or re.search(r"\d", c.text) for c in claims)


def _has_date(ins: Insight, ev) -> bool:
    return bool(ev.classification.effective_dates) or bool(DATE_RE.search(_text(ins.what_happened)))


def _action_due(ins: Insight) -> bool:
    return any(a.due and re.search(r"\d", a.due) for a in ins.actions)


def _bank_forecast(ins: Insight, *indicators: str) -> bool:
    refs = [s for c in ins.bank_view for s in c.sources if s.startswith("bank:fc-")]
    return any(r.startswith(f"bank:fc-{i}") for r in refs for i in indicators) if indicators else bool(refs)


def _bank_any(ins: Insight) -> bool:
    return any(c.kind == "bank_view" for c in ins.bank_view)


def _mentions(ins: Insight, pattern: str, sections=("why_it_matters", "risks", "opportunities", "actions", "takeaway")) -> bool:
    return bool(re.search(pattern, " ".join(c.text for s, c in all_claims(ins) if s in sections), re.I))


def _money_counted(ins: Insight) -> bool:
    s = (ins.scenarios or [None])[0]
    return s is not None  # сумма посчитана калькулятором или честно отмечено «заметного влияния нет»


def _personal(ins: Insight, ev=None) -> bool:
    return _src(ins.why_it_matters, "profile:", "graph:", "peers:")


# (id, название, шаблон в тексте события, факторы, чек-лист: [(что должно быть в разборе, проверка)])
KINDS: list[tuple[str, str, str, set[str], list]] = [
    ("cb_rate", "Решение по ключевой ставке", r"ключев\w* ставк|совет\w* директоров банка россии|заседани\w* (?:цб|банка россии)",
     {"key_rate"}, [
         ("Новое значение ставки", lambda i, e: bool(re.search(PCT, _text(i.what_happened)))),
         ("Прогноз ставки Альфа-Банка", lambda i, e: _bank_forecast(i, "key_rate")),
         ("Как изменятся платежи по долгу или доход по депозитам",
          lambda i, e: _mentions(i, r"кредит|долг|плавающ|процент|депозит|займ") or _money_counted(i)),
         ("Что сделать и к какому сроку", lambda i, e: _action_due(i)),
     ]),
    ("duty", "Пошлина, тариф или квота", r"пошлин|тариф|утильсбор|квот[аыу]|акциз", set(), [
        ("Размер пошлины или тарифа", lambda i, e: _has_number(i.what_happened)),
        ("С какой даты действует", _has_date),
        ("Кого из поставщиков или покупателей касается", lambda i, e: _src(i.why_it_matters, "graph:", "profile:FTS")
         or _mentions(i, r"поставщик|покупател|контрагент|импорт|экспорт")),
        ("Как изменятся цены закупок или продаж", lambda i, e: _mentions(i, r"цен|себестоим|закуп|маржин")),
    ]),
    ("tax", "Налоговое изменение", r"налог|ндс|ндфл|усн|страхов\w* взнос|ставк\w* взнос", {"tax_regulation"}, [
        ("Что именно меняется: ставка, база или льгота", lambda i, e: _has_number(i.what_happened)),
        ("С какой даты действует", _has_date),
        ("Касается ли это компании", _personal),
        ("Что подготовить и к какому сроку", lambda i, e: _action_due(i)),
    ]),
    ("sanctions", "Санкции и ограничения торговли",
     r"санкц|эмбарго|запрет\w* на (?:ввоз|вывоз|импорт|экспорт)|ограничени\w* на (?:ввоз|вывоз)",
     {"sanctions_trade", "geopolitics"}, [
         ("Какие страны, товары или банки затронуты", lambda i, e: bool(e.classification.countries) or _has_number(i.what_happened)),
         ("Связь с ВЭД и контрагентами компании", lambda i, e: _src(i.why_it_matters, "graph:", "profile:FTS")
          or _mentions(i, r"вэд|импорт|экспорт|поставщик|контрагент")),
         ("Риски для платежей и логистики", lambda i, e: _mentions(i, r"платеж|расчет|расчёт|логист|поставк|банк")),
         ("План на случай ухудшения", lambda i, e: bool(i.actions)),
     ]),
    ("subsidy", "Господдержка и льготы",
     r"субсиди|льготн\w* (?:кредит|ставк|программ)|господдержк|грант|компенсаци\w* (?:част|затрат)", set(), [
        ("Кто может получить поддержку", lambda i, e: _mentions(i, r"мсп|малы|средн|компани|предпринимат|получ|участ",
                                                               ("what_happened", "why_it_matters", "takeaway"))),
        ("Размер или условия поддержки", lambda i, e: _has_number(i.what_happened)),
        ("Подходит ли это компании", _personal),
        ("Что сделать, чтобы воспользоваться", lambda i, e: bool(i.actions)),
    ]),
    ("regulation", "Новое регулирование", r"закон|постановлени|маркировк|требовани|вступ\w* в силу|обязательн|правил", {"tax_regulation"}, [
        ("Что именно меняется", lambda i, e: any(c.kind == "fact" for c in i.what_happened)),
        ("С какой даты действует", _has_date),
        ("Касается ли это компании", _personal),
        ("Кто в компании готовится и к какому сроку", lambda i, e: _action_due(i) and all(a.owner for a in i.actions)),
    ]),
    ("fx", "Курс рубля и валюты", r"курс\w* (?:рубл|доллар|юан|евро)|рубл\w* (?:укреп|ослаб)|девальвац", {"fx_rub", "china_trade"}, [
        ("Текущий курс или изменение", lambda i, e: _has_number(i.what_happened)),
        ("Прогноз курса Альфа-Банка", lambda i, e: _bank_forecast(i, "usd_rub", "cny_rub", "eur_rub")),
        ("Сколько валюты в расчётах компании", lambda i, e: _src(i.why_it_matters, "profile:FTS", "graph:")
         or _mentions(i, r"импорт|экспорт|валют|вэд")),
        ("Как защититься от колебаний курса", lambda i, e: _mentions(i, r"хедж|форвард|фиксир|валютн|оговорк", ("actions",))
         or any(a.action_type == "hedge" for a in i.actions)),
    ]),
]
DEFAULT_KIND = ("market", "Рынок, спрос и экономика", "", set(), [
    ("Цифры из новости", lambda i, e: _has_number(i.what_happened)),
    ("Связь с бизнесом компании", _personal),
    ("Позиция Альфа-Банка или честное «материалов нет»", lambda i, e: bool(i.bank_view)),
    ("Что сделать и к какому сроку", lambda i, e: _action_due(i)),
])


def event_kind(ev) -> tuple:
    """Тип события: сначала по тексту заголовка и краткого содержания, затем по главному фактору."""
    head = f"{ev.title} {ev.classification.summary}".lower()
    for k in KINDS:
        if k[2] and re.search(k[2], head):
            return k
    top = {h.factor for h in ev.classification.factors[:2]}
    for k in KINDS:
        if k[3] & top:
            return k
    return DEFAULT_KIND


def kind_brief(ev) -> dict:
    """Для контекста модели: какой это тип события и на что разбор обязан ответить."""
    k = event_kind(ev)
    return {"type": k[1], "must_answer": [label for label, _ in k[4]]}


def checklist(ins: Insight, ev) -> dict:
    k = event_kind(ev)
    items = []
    for label, test in k[4]:
        try:
            ok = bool(test(ins, ev))
        except Exception:  # проверка — подсказка читателю, она не должна ломать разбор
            ok = False
        items.append({"label": label, "ok": ok})
    return {"id": k[0], "title": k[1], "items": items, "passed": sum(x["ok"] for x in items)}


# --------------------------------------------------------------- факты: сколько источников подтверждают
_STOP = {"компании", "компаний", "россии", "также", "этого", "который", "которые", "будет", "может", "после", "более"}


def _words(s: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[а-яёa-z]{5,}", (s or "").lower()) if w not in _STOP}


def fact_support(claim_text: str, news: list) -> list[str]:
    """Издания, в тексте которых есть все числа факта, а без чисел — большая часть его слов."""
    nums = {_num(x) for x in NUM_RE.findall(claim_text)}
    words = _words(claim_text)
    outlets: list[str] = []
    for n in news:
        body = f"{n.title} {n.text}"
        found = {_num(x) for x in NUM_RE.findall(body)}
        ok = nums <= found if nums else bool(words) and len(words & _words(body)) / len(words) >= 0.5
        name = n.source_title or n.source
        if ok and name not in outlets:
            outlets.append(name)
    return outlets


# --------------------------------------------------------------- позиция банка: дата материала
def _bank_date(sid: str) -> str | None:
    kb = get_knowledge()
    ref = sid.partition(":")[2]
    if ref.startswith("fc-"):
        indicator, _, period = ref[3:].rpartition("-")
        return (kb.forecast(indicator, period) or {}).get("date")
    th = next((t for t in kb.theses if t["id"] == ref), None)
    ch = None if th else next((c for c in kb.chunks if c.id == ref), None)
    doc_id = th["doc_id"] if th else ch.doc_id if ch else ""
    return kb.doc(doc_id).get("date") if doc_id else None


def _age_days(d: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(d[:10])).days if d else None
    except ValueError:
        return None


# --------------------------------------------------------------- цифры новости и прогноз банка рядом
# фактор → (как найти число в новости, какой прогноз банка взять)
NUMBER_PAIRS = {
    "key_rate": (r"ключев\w* ставк\w*[^.%]{0,60}?(\d+(?:[.,]\d+)?)\s?%", "ключевая ставка"),
    "inflation": (r"инфляци\w*[^.%]{0,60}?(\d+(?:[.,]\d+)?)\s?%", "инфляция"),
    "labor_market": (r"зарплат\w*[^.%]{0,60}?(\d+(?:[.,]\d+)?)\s?%", "рост зарплат"),
}


def number_pairs(ev) -> list[dict]:
    out = []
    factors = [h.factor for h in ev.classification.factors[:3]]
    for f in factors:
        if f not in NUMBER_PAIRS:
            continue
        fc = headline_forecast(f)
        if not fc or fc.get("value") is None:
            continue
        pattern, title = NUMBER_PAIRS[f]
        for n in ev.news:
            m = re.search(pattern, f"{n.title}. {n.text}", re.I)
            if not m:
                continue
            news_val = float(m.group(1).replace(",", "."))
            gap = round(news_val - float(fc["value"]), 2)
            out.append({"factor": f, "title": title, "news_value": news_val, "news_source": n.source_title or n.source,
                        "news_ref": f"news:{n.id}", "bank_value": fc["value"], "bank_period": fc["period"],
                        "bank_date": fc.get("date"), "bank_ref": fc["ref"], "gap_pp": gap, "differs": abs(gap) >= 0.5})
            break
    return out


# --------------------------------------------------------------- что изменилось с прошлого события сюжета
def story_delta(ev) -> dict | None:
    if not ev.story_id:
        return None
    from app.news.stories import get_story

    st = get_story(ev.story_id) or {}
    events = st.get("events") or []
    idx = next((k for k, e in enumerate(events) if e["event_id"] == ev.id), None)
    if not idx:  # первое событие сюжета или его нет в хронологии — сравнивать не с чем
        return None
    # сюжет бывает широким (десятки событий про ставку и кредиты): сравниваем с ближайшим по смыслу прошлым событием
    # того же фактора, а не просто с предыдущим по времени — иначе «раньше» окажется про другое
    main = ev.classification.factors[0].factor if ev.classification.factors else None
    mine = _words(f"{ev.title} {ev.classification.summary}")

    def close(e: dict) -> float:
        w = _words(f"{e.get('title', '')} {e.get('summary', '')}")
        return len(mine & w) / max(1, min(len(mine), len(w)))

    earlier = [e for e in events[:idx] if e.get("factor") == main and e["event_id"] != ev.id]
    scored = sorted(((close(e), k, e) for k, e in enumerate(earlier)), key=lambda x: (x[0], x[1]), reverse=True)
    if not scored or scored[0][0] < 0.25:
        return None
    prev = scored[0][2]
    now_nums = list(dict.fromkeys(re.findall(PCT + r"|\d+(?:[.,]\d+)?\s*(?:млрд|млн)\s*(?:₽|руб\w*)", ev.classification.summary)))
    prev_text = f"{prev.get('title', '')} {prev.get('summary', '')}"
    prev_nums = {_num(x) for x in NUM_RE.findall(prev_text)}
    new_nums = [x for x in now_nums if not ({_num(y) for y in NUM_RE.findall(x)} <= prev_nums)]
    days = None
    if ev.published_at and prev.get("published_at"):
        days = _age_days(prev["published_at"], date.fromisoformat(ev.published_at[:10]))
    return {"story_title": st.get("title"), "count": len(events), "position": idx + 1, "similarity": round(scored[0][0], 2),
            "prev": {"event_id": prev["event_id"], "title": prev.get("title"), "date": prev.get("published_at"),
                     "summary": prev.get("summary")},
            "now": ev.classification.summary, "new_numbers": new_nums[:4], "days_since_prev": days}


# --------------------------------------------------------------- сборка
def review(ins: Insight, ev, today: date | None = None) -> Insight:
    """Дополняет разбор проверяемыми пометками. Тексты разбора не меняются."""
    today = today or datetime.now(UTC).date()
    for c in ins.what_happened:
        if c.kind == "fact":
            c.support = fact_support(c.text, ev.news)
    for c in ins.bank_view:
        if c.kind != "bank_view":
            continue
        dates = [d for d in (_bank_date(s) for s in c.sources if s.startswith("bank:")) if d]
        if dates:
            c.as_of = max(dates)[:10]
            age = _age_days(c.as_of, today)
            c.stale = age is not None and age > BANK_FRESH_DAYS
    try:
        delta = story_delta(ev)
    except Exception:  # сюжет — дополнение, разбор без него не ломается
        delta = None
    ins.review = {"kind": checklist(ins, ev), "numbers": number_pairs(ev), "story_delta": delta,
                  "sources_n": len({n.source_title or n.source for n in ev.news})}
    return ins
