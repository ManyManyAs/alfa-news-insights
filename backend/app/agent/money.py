"""«Деньги на кону» v2: ИИ отбирает события, калькулятор считает суммы.

Правило владельца остаётся в силе: суммы в рублях считает только детерминированный калькулятор (scenario.py).
Модель решает другое — то, в чём правила ошибались (новость «в Кении обрушилось здание» попадала в рычаг «зарплаты»):
1. касается ли событие денег ЭТОЙ компании через конкретный рычаг (ставка, курс, зарплаты, спрос, цены закупок);
2. в какую сторону событие толкает рычаг для компании — хуже плана или лучше;
3. насколько сильно (1–3) и почему — одной фразой для клиента.

Кандидатов больше, чем раньше (события важности от «низкой» и выше, до 25 штук): ИИ отсеивает лишнее.
Если модель недоступна (бюджет, mock/replay без сохранённого ответа), работает прежний отбор правилами: только события
средней и высокой важности. В ответе всегда есть чувствительность компании (сколько стоит отклонение рычага на
типовую величину) — карточка не бывает пустой.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.agent.humanize import humanize_text
from app.agent.scenario import scenarios as scenario_set
from app.security.guard import UNTRUSTED_NOTICE, guard_output, sanitize_untrusted

log = logging.getLogger(__name__)

LEVER_TITLE = {"rate": "Ставка по кредитам и депозитам", "fx": "Курс рубля", "wages": "Зарплаты и ФОТ",
               "demand": "Спрос и выручка", "costs": "Цены закупок"}
PROTECT_ACTIONS = {
    "rate": "зафиксировать ставку по части кредитов или рефинансировать долг",
    "fx": "захеджировать валютные платежи (форвард на ближайшие поставки)",
    "costs": "зафиксировать цены в договорах с ключевыми поставщиками",
    "wages": "заложить индексацию зарплат в бюджет заранее",
    "demand": "проверить план продаж и запас по оборотному капиталу",
}

MONEY_SYSTEM = """Ты — аналитик Альфа-Банка. Для компании-клиента реши, какие события из ленты реально могут изменить её
деньги за 12 месяцев через указанный рычаг. Суммы НЕ считай — их считает калькулятор банка.
{notice}
Для каждого пункта ответь:
- relevant: true, только если есть понятная причинно-следственная связь «событие → рычаг → деньги этой компании»
  (отрасль, регион, продукты банка, ВЭД, контрагенты из профиля). Происшествия, политика без экономических мер,
  события в других странах без связи с ВЭД компании, общие новости — false;
- direction: "worse" — событие толкает рычаг против компании (дороже деньги, слабее рубль при импорте, выше зарплаты,
  ниже спрос, дороже закупки), "better" — в пользу компании, "unclear" — неясно;
- strength: 1 — слабый сигнал, 2 — заметный, 3 — сильный и прямой;
- why: одна фраза по-русски для клиента, до 140 символов, без технических кодов и без сумм.
Верни ТОЛЬКО JSON: {{"items": [{{"key": "...", "relevant": true, "direction": "worse|better|unclear", "strength": 1, "why": "..."}}]}}"""


class MoneyItem(BaseModel):
    key: str
    relevant: bool = False
    direction: str = "unclear"
    strength: int = Field(1, ge=1, le=3)
    why: str = ""


class MoneyOut(BaseModel):
    items: list[MoneyItem] = []


def _company_line(p) -> str:
    prods = ", ".join(p.products[:6]) if p.products else "нет данных"
    fts = ", ".join(p.fts_countries[:4]) if p.fts and p.fts_countries else "нет"
    return (f"Отрасль: {p.okved_name} (код {p.okved}); регион: {p.region or 'нет данных'}; сотрудники: {p.workers or 'нет данных'}; "
            f"продукты банка: {prods}; страны ВЭД: {fts}")


def candidates(p, role, ranked: list, mat: dict, importance_level, cached_insight) -> list[dict]:
    """События ленты с денежным эффектом по калькулятору: (событие, рычаг, минус, плюс)."""
    out = []
    for s in ranked[:25]:
        if importance_level(s.score, mat.get(s.event.id))["level"] < 1:
            continue
        ins = cached_insight(s.event.id, p.uid, role)
        if ins is not None and ins.materiality is not None and ins.materiality == 0:
            continue
        cls = s.event.classification
        sc = ([x.model_dump() for x in ins.scenarios] if ins and ins.scenarios else
              scenario_set(p, s.matched_factors, reasons=s.reasons, effective_dates=cls.effective_dates, regions=cls.regions,
                           countries=cls.countries))
        if not sc or sc[0].get("negligible"):
            continue
        by = {x["name"]: x for x in sc}
        rows: dict[str, dict] = {}
        for name in ("negative", "positive"):
            for r in by.get(name, {}).get("rows", []):
                lv = rows.setdefault(r["lever"], {"down": 0, "up": 0, "title": r["title"]})
                v = r["effect_rub"]
                if name == "negative" and v < 0:
                    lv["down"] = min(lv["down"], v)
                if name == "positive" and v > 0:
                    lv["up"] = max(lv["up"], v)
        news = s.event.news[0] if getattr(s.event, "news", None) else None
        lead = (news.text if news else "")[:260]
        for lever, v in rows.items():
            if v["down"] or v["up"]:
                out.append({"key": f"{s.event.id}|{lever}", "event_id": s.event.id, "title": s.event.title, "lead": lead,
                            "effect": s.effect, "lever": lever, "lever_title": v["title"], "down": v["down"], "up": v["up"],
                            "level": importance_level(s.score, mat.get(s.event.id))["level"],
                            "reason": (s.reasons[0].get("why") if s.reasons else "") or ""})
    return out


def ai_check(p, role, cands: list[dict]) -> tuple[dict[str, dict] | None, dict]:
    """Одно обращение к модели на компанию и роль: что из кандидатов реально касается денег компании."""
    from app.llm.client import LLMUnavailable, get_llm

    if not cands:
        return {}, {"used": False, "why": "нет кандидатов"}
    llm = get_llm()
    lines = [f"{i + 1}. key={c['key']} · рычаг: {LEVER_TITLE.get(c['lever'], c['lever_title'])} · событие: "
             f"{sanitize_untrusted(c['title'])[0][:160]} — {sanitize_untrusted(c['lead'])[0][:220]}" for i, c in enumerate(cands)]
    msg = [{"role": "system", "content": MONEY_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
           {"role": "user", "content": f"<company>{_company_line(p)}; роль пользователя: {role or 'не указана'}</company>\n"
                                       "<items>\n" + "\n".join(lines) + "\n</items>"}]
    try:
        data, res = llm.complete_json("money_check", msg, MoneyOut, max_tokens=1800)
    except (LLMUnavailable, ValueError) as e:
        return None, {"used": False, "why": str(e)[:160]}
    keys = {c["key"] for c in cands}
    out = {}
    for it in data.get("items", []):
        if it["key"] in keys:
            why = guard_output(humanize_text(it.get("why") or ""), set(), "ваша компания")[:200]
            out[it["key"]] = {**it, "why": why}
    return out, {"used": True, "model": res.model, "cost_rub": round(res.cost_rub, 3), "cached": res.cached}


def money_view(p, role, ranked: list, mat: dict, importance_level, cached_insight, revenue) -> dict:
    cands = candidates(p, role, ranked, mat, importance_level, cached_insight)
    verdict, ai = ai_check(p, role, cands)
    levers: dict[str, dict] = {}
    used: set[str] = set()
    for c in cands:
        if verdict is None:  # без модели — прежний отбор правилами: только средняя и высокая важность
            ok, info = c["level"] >= 2, {"direction": "unclear", "strength": 1, "why": c["reason"]}
        else:
            info = verdict.get(c["key"]) or {}
            ok = bool(info.get("relevant"))
        if not ok:
            continue
        used.add(c["event_id"])
        lv = levers.setdefault(c["lever"], {"lever": c["lever"], "title": LEVER_TITLE.get(c["lever"], c["lever_title"]),
                                             "down": 0, "up": 0, "events": [], "worse": 0, "better": 0})
        lv["down"] = min(lv["down"], c["down"])
        lv["up"] = max(lv["up"], c["up"])
        d = info.get("direction", "unclear")
        if d in ("worse", "better"):
            lv[d] += int(info.get("strength") or 1)
        if not any(e["event_id"] == c["event_id"] for e in lv["events"]):
            lv["events"].append({"event_id": c["event_id"], "title": c["title"], "effect": c["effect"], "direction": d,
                                 "strength": int(info.get("strength") or 1), "why": info.get("why") or ""})
    rows = sorted((v for v in levers.values() if v["down"] or v["up"]), key=lambda v: -(abs(v["down"]) + v["up"]))
    for v in rows:
        v["action"] = PROTECT_ACTIONS.get(v["lever"], "")
        v["events"] = sorted(v["events"], key=lambda e: -e["strength"])[:4]
        v["tilt"] = "worse" if v["worse"] > v["better"] else "better" if v["better"] > v["worse"] else "even"
    down = sum(v["down"] for v in rows)
    up = sum(v["up"] for v in rows)
    # чувствительность: к чему компания чувствительна, даже когда событий с деньгами нет
    sens = []
    try:
        sens = scenario_set(p, ["key_rate", "fx_rub", "labor_market"])[0].get("sensitivity", [])
    except Exception:  # чувствительность — справка, не должна ломать карточку
        log.exception("Чувствительность не посчитана")
    return {"up": round(up), "down": round(down), "protect": round(sum(v["down"] for v in rows if v["action"])), "levers": rows,
            "events_used": len(used), "candidates": len({c["event_id"] for c in cands}),
            "dropped": len({c["event_id"] for c in cands} - used), "ai": ai,
            "sensitivity": [{"lever": s["lever"], "title": s["title"], "effect_rub": s["effect_rub"]} for s in sens],
            "share_up": round(up / revenue, 4) if revenue else None, "share_down": round(down / revenue, 4) if revenue else None}
