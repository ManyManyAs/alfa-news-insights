"""ИИ-сито ленты: убрать заранее то, что компании не касается, чтобы не мозолило глаз.

Первая модель (реранк, Gemma) ставит карточке 0–3. Если она поставила 0 («не про компанию»), вторая модель (Qwen)
независимо проверяет это решение. Карточка скрывается, только когда обе согласны. Не скрываются никогда:
- события высокой важности по профилю (связь ≥ IMPORTANCE_HIGH);
- темы, за которыми пользователь следит сам.

Скрытое не теряется: оно в «Что сервис не показал и почему» с причиной. Решения хранятся в таблице ai_screen
и при следующих открытиях ленты не тратят бюджет. Без модели ничего не скрывается.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from app.data.models import CompanyProfile
from app.llm.client import LLMUnavailable, get_llm
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted
from app.storage import db

log = logging.getLogger(__name__)
KEEP_SCORE = 1.0  # связь «высокая» по профилю — карточку не прячем, даже если обе модели против

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_screen (
    company_uid TEXT, role TEXT, event_id TEXT, relevance INTEGER, hidden INTEGER DEFAULT 0, why TEXT, checker TEXT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (company_uid, role, event_id)
);
"""
_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        with db.tx() as conn:
            conn.executescript(SCHEMA)
        _ready = True


class CheckItem(BaseModel):
    n: int
    relevant: bool
    why: str = ""


class CheckOut(BaseModel):
    items: list[CheckItem]


CHECK_SYSTEM = """Ты проверяешь решение другой модели: она сочла эти новости НЕ относящимися к бизнесу компании.
Для каждой новости реши, есть ли у компании реальный канал влияния: её отрасль, продукты банка (кредит, лизинг,
депозит, зарплатный проект), ВЭД и страны, отрасли покупателей и поставщиков, регион, стоимость денег.
relevant=true — если канал есть и он заметен для денег компании (тогда новость оставляем). relevant=false — если
канала нет или он мизерный. Не придумывай далёких цепочек: «всё влияет на всё» — это false.
{notice}
Ответ — JSON: {{"items": [{{"n": <номер>, "relevant": true|false, "why": "одна фраза про компанию"}}]}}"""


def screened(company_uid: str, role: str | None) -> dict[str, str]:
    """Скрытые ИИ события компании и роли: {event_id: причина}."""
    ensure_schema()
    rows = db.query("SELECT event_id, why FROM ai_screen WHERE company_uid=? AND role=? AND hidden=1", (company_uid, role or "-"))
    return {r["event_id"]: r["why"] or "" for r in rows}


def verify_and_store(top: list, got: dict, profile: CompanyProfile, role: str | None) -> dict[str, str]:
    """Оценки реранка сохраняются; нули проверяет вторая модель. Возвращает скрытые: {event_id: причина}."""
    ensure_schema()
    r = role or "-"
    have = {x["event_id"] for x in db.query("SELECT event_id FROM ai_screen WHERE company_uid=? AND role=?", (profile.uid, r))}
    cands = []
    for i, s in enumerate(top, 1):
        it = got.get(i)
        if not it or s.event.id in have:
            continue
        protected = s.score >= KEEP_SCORE or any(x.get("factor") == "watch" for x in s.reasons)
        if it["relevance"] == 0 and not protected:
            cands.append((i, s, it))
    verdict: dict[str, tuple[bool, str]] = {}
    checker = ""
    if cands:
        llm = get_llm()
        lines = []
        for k, (_, s, it) in enumerate(cands, 1):
            title, _ = sanitize_untrusted(s.event.title)
            summ, _ = sanitize_untrusted(s.event.classification.summary or "")
            lines.append(f"{k}. {title}\n   Суть: {summ[:280]}\n   Первая модель: {it.get('why', '')[:160]}")
        msgs = [{"role": "system", "content": CHECK_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                {"role": "user", "content": f"Компания: {json.dumps(profile.llm_view(), ensure_ascii=False)}\n"
                                            f"Роль: {role or 'руководитель'}\n\n<news>\n" + "\n".join(lines) + "\n</news>"}]
        try:
            data, res = llm.complete_json("screen_check", msgs, CheckOut, max_tokens=800, temperature=0.0)
            checker = res.model
            for c in data["items"]:
                if 1 <= c["n"] <= len(cands):
                    verdict[cands[c["n"] - 1][1].event.id] = (bool(c["relevant"]), c.get("why", ""))
        except (LLMUnavailable, ValueError) as e:  # без проверки ничего не прячем
            log.info("Проверка ИИ-сита недоступна: %s", e)
    hidden: dict[str, str] = {}
    with db.tx() as conn:
        for i, s in enumerate(top, 1):
            it = got.get(i)
            if not it or s.event.id in have:
                continue
            v = verdict.get(s.event.id)
            hide = bool(v and not v[0])
            why = (v[1] if v else "") or it.get("why", "")
            if hide:
                hidden[s.event.id] = why
            conn.execute("INSERT OR REPLACE INTO ai_screen(company_uid, role, event_id, relevance, hidden, why, checker) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (profile.uid, r, s.event.id, int(it["relevance"]), int(hide), why[:300], checker if v else ""))
    return {**screened(profile.uid, role), **hidden}
