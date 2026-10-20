"""Сюжеты: цепочки связанных событий за несколько недель.

Событие — это одна новость в пересказах разных СМИ (окно 2 дня). Сюжет шире: решение ЦБ по ставке →
реакция рынка ОФЗ → пересмотр прогнозов. Клиенту важно видеть развитие, а не пять отдельных карточек,
поэтому лента сворачивает сюжет в одну карточку с таймлайном.

Правило связи (объяснимое, без LLM): общий главный фактор + лексическая близость заголовков/лидов
в окне STORY_WINDOW_DAYS. Порог ниже, чем у склейки в событие, но с обязательным общим фактором.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

from app.news.dedup import _dt, lead, overlap, stems
from app.news.models import EventClassification
from app.storage import db

STORY_WINDOW_DAYS = 45
STORY_THRESHOLD = 0.25


def _top_factors(cls: EventClassification, n: int = 2) -> list[str]:
    return [h.factor for h in cls.factors[:n]]


def story_score(a_title: str, a_text: str, a_cls: EventClassification,
                b_title: str, b_text: str, b_cls: EventClassification) -> float:
    """0..1: насколько два события — части одного сюжета. 0, если нет общего главного фактора."""
    fa, fb = _top_factors(a_cls), _top_factors(b_cls)
    if not fa or not fb or (fa[0] not in fb and fb[0] not in fa):
        return 0.0
    title_ov = overlap(stems(a_title), stems(b_title))
    lead_ov = overlap(stems(lead(a_title, a_text, 30)), stems(lead(b_title, b_text, 30)))
    same_type = 0.1 if a_cls.event_type == b_cls.event_type and a_cls.event_type != "other" else 0.0
    return min(1.0, 0.5 * title_ov + 0.4 * lead_ov + same_type)


def _event_lead(event_id: str) -> tuple[str, str]:
    r = db.query_one("SELECT title, text FROM news WHERE event_id=? ORDER BY published_at LIMIT 1", (event_id,))
    return (r["title"], r["text"] or "") if r else ("", "")


def assign_story(event_id: str) -> str | None:
    """Ищет сюжет для события; при совпадении с одиночным событием создаёт новый сюжет из двух."""
    row = db.query_one("SELECT id, title, published_at, classification, story_id FROM events WHERE id=?", (event_id,))
    if not row or not row["classification"] or row["story_id"]:
        return row["story_id"] if row else None
    cls = EventClassification.model_validate_json(row["classification"])
    if not cls.factors:
        return None
    title, text = _event_lead(event_id)
    t = _dt(row["published_at"])
    best, best_score = None, 0.0
    for c in db.query("SELECT id, title, published_at, classification, story_id FROM events "
                      "WHERE id != ? AND classification IS NOT NULL ORDER BY published_at DESC LIMIT 400", (event_id,)):
        tc = _dt(c["published_at"])
        if t and tc and abs(t - tc) > timedelta(days=STORY_WINDOW_DAYS):
            continue
        ccls = EventClassification.model_validate_json(c["classification"])
        ct, ctext = _event_lead(c["id"])
        s = story_score(title, text, cls, ct, ctext, ccls)
        if s > best_score:
            best, best_score = c, s
    if best is None or best_score < STORY_THRESHOLD:
        return None
    with db.tx() as conn:
        story_id = best["story_id"]
        if not story_id:
            story_id = f"st-{uuid.uuid4().hex[:10]}"
            conn.execute("INSERT INTO stories(id, title, factor, event_ids, first_at, last_at) VALUES (?,?,?,?,?,?)",
                         (story_id, best["title"], cls.factors[0].factor, json.dumps([best["id"]]),
                          best["published_at"], best["published_at"]))
            conn.execute("UPDATE events SET story_id=? WHERE id=?", (story_id, best["id"]))
        st = conn.execute("SELECT event_ids, first_at, last_at FROM stories WHERE id=?", (story_id,)).fetchone()
        ids = json.loads(st["event_ids"] or "[]")
        if event_id not in ids:
            ids.append(event_id)
        dates = [d for d in (st["first_at"], st["last_at"], row["published_at"]) if d]
        first, last = (min(dates), max(dates)) if dates else (None, None)
        # заголовок сюжета — самое свежее событие: пользователь видит, «где мы сейчас»
        title_now = row["title"] if row["published_at"] and row["published_at"] == last else None
        conn.execute("UPDATE stories SET event_ids=?, first_at=?, last_at=?, title=COALESCE(?, title) WHERE id=?",
                     (json.dumps(ids), first, last, title_now, story_id))
        conn.execute("UPDATE events SET story_id=? WHERE id=?", (story_id, event_id))
    return story_id


def rebuild_stories() -> int:
    """Пересобирает сюжеты по всем событиям в хронологическом порядке (после импорта/смены правил)."""
    with db.tx() as conn:
        conn.execute("DELETE FROM stories")
        conn.execute("UPDATE events SET story_id=NULL")
    for r in db.query("SELECT id FROM events WHERE classification IS NOT NULL ORDER BY published_at"):
        assign_story(r["id"])
    return db.query_one("SELECT COUNT(*) n FROM stories")["n"]


def get_story(story_id: str) -> dict | None:
    st = db.query_one("SELECT * FROM stories WHERE id=?", (story_id,))
    if not st:
        return None
    rows = db.query("SELECT id, title, published_at, classification FROM events WHERE story_id=? ORDER BY published_at",
                    (story_id,))
    timeline = []
    for r in rows:
        cls = EventClassification.model_validate_json(r["classification"]) if r["classification"] else None
        main = cls.factors[0] if cls and cls.factors else None
        timeline.append({"event_id": r["id"], "title": r["title"], "published_at": r["published_at"],
                         "summary": cls.summary if cls else "", "factor": main.factor if main else None,
                         "direction": main.direction if main else None,
                         "effective_dates": cls.effective_dates if cls else []})
    return {"id": st["id"], "title": st["title"], "factor": st["factor"], "first_at": st["first_at"],
            "last_at": st["last_at"], "events": timeline}
