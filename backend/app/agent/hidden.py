"""Неочевидные последствия события для компании — эффекты второго порядка, которых не видно из заголовка.

Две модели: автор (DeepSeek) предлагает до двух цепочек «событие → … → деньги компании» только через реальные связи
компании (отрасли покупателей и поставщиков из графа платежей, продукты банка, ВЭД); проверяющий (GLM, независимая
модель) оставляет только те, где каждая ступень следует из предыдущей и связь действительно есть в данных.
Натянутое отбрасывается: пустой список лучше «всё влияет на всё». Суммы здесь не называются — их считает калькулятор.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from app.agent.humanize import humanize_text
from app.agent.schemas import HiddenEffect
from app.llm.client import LLMUnavailable, get_llm
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted

log = logging.getLogger(__name__)
MAX_ITEMS = 2


class HiddenItem(BaseModel):
    text: str
    effect: str = "risk"
    chain: list[str] = Field(default_factory=list)
    via: list[str] = Field(default_factory=list)
    plausibility: int = 2


class HiddenOut(BaseModel):
    items: list[HiddenItem] = Field(default_factory=list)


class CheckItem(BaseModel):
    n: int
    keep: bool
    why: str = ""


class CheckOut(BaseModel):
    items: list[CheckItem] = Field(default_factory=list)


HIDDEN_SYSTEM = """Ты — аналитик Альфа-Банка. Найди НЕОЧЕВИДНЫЕ последствия события для конкретной компании — эффекты
второго порядка, которых не видно из заголовка: через её покупателей и поставщиков, продукты банка, ВЭД.
{notice}
Правила:
- Каждое последствие идёт через связь из списка links (это реальные данные компании); в поле via — id связи из links.
- Роли контрагентов не путай: ПОСТАВЩИКИ — те, кому компания платит (их проблемы меняют затраты, сроки и надёжность
  поставок компании); ПОКУПАТЕЛИ — те, кто платит компании (их проблемы меняют выручку, спрос и сроки оплаты).
  Поставщики не покупают у компании, покупатели ей не поставляют.
- Не повторяй прямые эффекты из already — они уже есть в разборе.
- chain — 3–4 коротких шага от события к деньгам компании, каждый следует из предыдущего.
- Суммы и проценты не называй, если их нет в событии.
- plausibility: 3 — вероятно, 2 — возможно. Натянутое (1) не пиши вовсе.
- Если правдоподобного неочевидного эффекта нет — верни пустой список. Пустой список лучше натянутого вывода.
- До 2 пунктов. text — одно предложение для клиента простым языком.
Ответ — JSON: {{"items": [{{"text": "...", "effect": "risk|opportunity", "chain": ["...", "..."],
"via": ["<id из links>"], "plausibility": 2}}]}}"""

CHECK_SYSTEM = """Ты — строгий проверяющий. Другая модель предложила неочевидные последствия события для компании.
Оставь пункт (keep=true), только если одновременно:
1) связь via есть в данных компании и по смыслу подходит к цепочке;
2) каждый шаг цепочки логично следует из предыдущего, без допущений «если вдруг»;
3) это не повтор прямого эффекта из already;
4) направление (риск или возможность) указано верно;
5) эффект заметен для денег компании, а не «всё влияет на всё»;
6) роли контрагентов не перепутаны: поставщики — затраты и поставки компании, покупатели — её выручка и оплата.
   «Поставщики отложат закупки у компании» или «покупатели сорвут поставки» — ошибка, keep=false.
Если сомневаешься — keep=false: лучше не показать, чем показать натяжку. why — одна короткая фраза.
{notice}
Ответ — JSON: {{"items": [{{"n": <номер>, "keep": true|false, "why": "..."}}]}}"""


def company_links(profile) -> list[dict]:
    """Связи компании, через которые может пройти эффект: id и подпись словами (те же, что в «Источниках»)."""
    from app.agent.orchestrator import _profile_label

    ids = [f"graph:{m['direction']}:{m['okved2']}" for m in profile.industry_mix if m.get("share", 0) >= 0.05][:8]
    ids += [f"profile:{p}" for p in profile.products]
    if profile.fts:
        ids.append("profile:FTS_FLAG")
    ids.append(f"profile:OKVED={(profile.okved or '')[:2]}")
    role = {"graph:in": "покупатели — платят компании: выручка, спрос, сроки оплаты",
            "graph:out": "поставщики — компания им платит: затраты, сроки и надёжность поставок"}
    return [{"id": i, "label": _profile_label(i, profile), **({"роль": role[i.rsplit(":", 1)[0]]} if i.startswith("graph:") else {})}
            for i in dict.fromkeys(ids)]


def find_hidden(ins, ctx, tracer) -> list[HiddenEffect]:
    llm = get_llm()
    links = company_links(ctx.profile)
    allowed = {x["id"] for x in links}
    already = [c.text for c in (ins.why_it_matters + ins.consequences.risks + ins.consequences.opportunities)][:8]
    title, _ = sanitize_untrusted(ctx.event.title)
    summ, _ = sanitize_untrusted(ctx.event.classification.summary or "")
    payload = {"event": {"title": title, "summary": summ[:600]}, "company": ctx.profile.llm_view(), "links": links, "already": already}
    msgs = [{"role": "system", "content": HIDDEN_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:14000]}]
    with tracer.step("hidden", "llm", input={"links": len(links)}) as rec:
        try:
            data, res = llm.complete_json("hidden_effects", msgs, HiddenOut, max_tokens=900, temperature=0.2)
            rec.update(model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_rub=res.cost_rub)
        except (LLMUnavailable, ValueError) as e:
            rec["output"] = {"skipped": str(e)[:200]}
            return []
        items = [it for it in data["items"] if it["plausibility"] >= 2 and it["effect"] in ("risk", "opportunity")
                 and any(v in allowed for v in it["via"]) and len(it["chain"]) >= 2][:MAX_ITEMS]
        rec["output"] = {"proposed": len(data["items"]), "kept_by_rules": len(items)}
    if not items:
        return []
    check_in = {"company": ctx.profile.llm_view(), "links": links, "already": already, "event": payload["event"],
                "items": [{"n": k + 1, "text": it["text"], "effect": it["effect"], "chain": it["chain"], "via": it["via"]}
                          for k, it in enumerate(items)]}
    msgs = [{"role": "system", "content": CHECK_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
            {"role": "user", "content": json.dumps(check_in, ensure_ascii=False)[:14000]}]
    with tracer.step("hidden:check", "llm", input={"items": len(items)}) as rec:
        try:
            data, res = llm.complete_json("hidden_check", msgs, CheckOut, max_tokens=500, temperature=0.0)
            rec.update(model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_rub=res.cost_rub)
        except (LLMUnavailable, ValueError) as e:  # без независимой проверки неочевидное не показываем
            rec["output"] = {"skipped": str(e)[:200]}
            return []
        keep = {c["n"]: c for c in data["items"] if c["keep"]}
        rec["output"] = {"checked": len(items), "kept": len(keep), "why": [c["why"] for c in data["items"]][:4]}
        checker = res.model
    out = []
    for k, it in enumerate(items, 1):
        if k not in keep:
            continue
        out.append(HiddenEffect(text=humanize_text(it["text"]), effect=it["effect"],
                                chain=[humanize_text(x) for x in it["chain"]][:4],
                                sources=[v for v in it["via"] if v in allowed], plausibility=it["plausibility"],
                                verified_by=checker.split("/")[-1], check_why=humanize_text(keep[k].get("why", ""))))
    return out
