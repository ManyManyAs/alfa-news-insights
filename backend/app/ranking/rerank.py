"""LLM-реранк верха ленты (Gemma): модель видит профиль компании и 15 карточек с причинами скоринга и ставит
каждой оценку 0–3 с короткой причиной. Итог: 0,6 × нормированный базовый скор + 0,4 × оценка модели.

Базовый скоринг остаётся главным (он объясним и детерминирован); модель лишь переставляет близкие по скору
карточки там, где правила не видят смысла (например, «спрос» в новости о продажах машин для ресторана).
Ответ кэшируется: повторное открытие ленты не тратит бюджет; без LLM порядок не меняется.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from app.data.models import CompanyProfile
from app.llm.client import LLMUnavailable, get_llm
from app.ranking.scorer import Scored
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted

log = logging.getLogger(__name__)
TOP_N = 15
W_BASE, W_LLM = 0.6, 0.4


class RerankItem(BaseModel):
    n: int
    relevance: int = Field(ge=0, le=3)
    why: str = ""


class RerankOut(BaseModel):
    items: list[RerankItem]


SYSTEM = """Ты аналитик Альфа-Банка. Оцени, насколько каждая новость важна для бизнеса конкретной компании-клиента
и роли пользователя: 3 — прямо влияет на деньги компании, 2 — заметно, 1 — косвенно (макрофон), 0 — не про неё.
Учитывай отрасль, продукты банка, ВЭД, контрагентов. Причина — одна короткая фраза про компанию (до 15 слов).
{notice}
Ответ — JSON: {{"items": [{{"n": <номер>, "relevance": 0..3, "why": "..."}}]}} для каждого номера."""


def rerank(items: list[Scored], profile: CompanyProfile, role: str | None) -> dict:
    """Возвращает {"applied": bool, "order": [event_id], "notes": {event_id: why}, "model": ...}."""
    llm = get_llm()
    top = items[:TOP_N]
    if not llm.enabled or len(top) < 2:
        return {"applied": False, "reason": "LLM недоступна" if not llm.enabled else "мало карточек"}
    lines = []
    for i, s in enumerate(top, 1):
        title, _ = sanitize_untrusted(s.event.title)
        summ, _ = sanitize_untrusted(s.event.classification.summary or "")
        why = "; ".join(r["why"] for r in s.reasons[:2])
        lines.append(f"{i}. {title}\n   Суть: {summ[:300]}\n   Скоринг: {why[:250]}")
    msgs = [{"role": "system", "content": SYSTEM.format(notice=UNTRUSTED_NOTICE)},
            {"role": "user", "content": f"Компания: {json.dumps(profile.llm_view(), ensure_ascii=False)}\n"
                                        f"Роль пользователя: {role or 'руководитель'}\n\n<news>\n" + "\n".join(lines) + "\n</news>"}]
    try:
        data, res = llm.complete_json("rerank", msgs, RerankOut, max_tokens=900, temperature=0.0)
    except (LLMUnavailable, ValueError) as e:
        log.info("Реранк недоступен: %s", e)
        return {"applied": False, "reason": str(e)[:200]}
    got = {it["n"]: it for it in data["items"] if 1 <= it["n"] <= len(top)}
    if len(got) < len(top) // 2:
        return {"applied": False, "reason": "модель оценила меньше половины карточек"}
    mx = max(s.score for s in top) or 1.0
    final = []
    for i, s in enumerate(top, 1):
        llm_rel = got.get(i, {}).get("relevance", 1)
        final.append((W_BASE * s.score / mx + W_LLM * llm_rel / 3, s.event.id))
    order = [eid for _, eid in sorted(final, key=lambda x: -x[0])] + [s.event.id for s in items[TOP_N:]]
    notes = {top[n - 1].event.id: {"relevance": it["relevance"], "why": it["why"]} for n, it in got.items()}
    return {"applied": True, "order": order, "notes": notes, "model": res.model, "cached": res.cached}
