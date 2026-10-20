"""Пайплайн: источники -> нормализация -> дедуп -> события -> классификация -> БД."""
from __future__ import annotations

import json
import logging
import uuid

from app.news.classify import classify
from app.news.dedup import content_hash, same_event
from app.news.models import Event, EventClassification, NewsItem
from app.news.sources import enabled_sources
from app.storage import db

log = logging.getLogger(__name__)


def _recent_events(limit: int = 300) -> list[dict]:
    rows = db.query("SELECT id, title, published_at, news_ids FROM events ORDER BY published_at DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        first = db.query_one("SELECT title, text, published_at FROM news WHERE event_id=? LIMIT 1", (r["id"],))
        if first:
            out.append({"id": r["id"], "title": first["title"], "text": first["text"] or "", "published_at": first["published_at"]})
    return out


def add_news(item: NewsItem, classify_now: bool = True) -> tuple[str, bool]:
    """Добавляет новость. Возвращает (event_id, is_new_event)."""
    h = content_hash(item.title, item.text)
    dup = db.query_one("SELECT event_id FROM news WHERE content_hash=? OR id=?", (h, item.id))
    if dup and dup["event_id"]:
        return dup["event_id"], False

    event_id = None
    for ev in _recent_events():
        if same_event(item.title, item.text, item.published_at, ev["title"], ev["text"], ev["published_at"]):
            event_id = ev["id"]
            break

    is_new = event_id is None
    with db.tx() as conn:
        if is_new:
            event_id = f"ev-{uuid.uuid4().hex[:10]}"
            conn.execute("INSERT INTO events(id, title, published_at, classification, news_ids) VALUES (?,?,?,?,?)",
                         (event_id, item.title, item.published_at, None, json.dumps([])))
        conn.execute(
            "INSERT OR REPLACE INTO news(id, source, source_title, url, title, text, published_at, content_hash, event_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (item.id, item.source, item.source_title, item.url, item.title, item.text, item.published_at, h, event_id),
        )
        ids = json.loads(conn.execute("SELECT news_ids FROM events WHERE id=?", (event_id,)).fetchone()["news_ids"] or "[]")
        ids.append(item.id)
        conn.execute("UPDATE events SET news_ids=? WHERE id=?", (json.dumps(ids), event_id))
    if is_new and classify_now:
        cls = classify(item)
        with db.tx() as conn:
            conn.execute("UPDATE events SET classification=? WHERE id=?", (cls.model_dump_json(), event_id))
    return event_id, is_new


def run_ingest(only: list[str] | None = None) -> dict:
    stats = {"fetched": 0, "new_events": 0, "merged": 0, "errors": []}
    for src in enabled_sources(only):
        try:
            items = src.fetch()
        except Exception as e:  # источник недоступен — продолжаем с остальными
            log.warning("Источник %s недоступен: %s", src.id, e)
            stats["errors"].append(f"{src.id}: {e}")
            continue
        stats["fetched"] += len(items)
        for it in items:
            _, is_new = add_news(it)
            stats["new_events" if is_new else "merged"] += 1
    return stats


def get_event(event_id: str) -> Event | None:
    row = db.query_one("SELECT * FROM events WHERE id=?", (event_id,))
    if not row:
        return None
    news = [NewsItem(**{k: r[k] for k in ("id", "source", "source_title", "url", "title", "text", "published_at")})
            for r in db.query("SELECT * FROM news WHERE event_id=? ORDER BY published_at", (event_id,))]
    cls = EventClassification.model_validate_json(row["classification"]) if row["classification"] else classify(news[0])
    return Event(id=row["id"], title=row["title"], published_at=row["published_at"], news=news, classification=cls)


def list_events(limit: int = 200) -> list[Event]:
    rows = db.query("SELECT id FROM events WHERE classification IS NOT NULL ORDER BY published_at DESC LIMIT ?", (limit,))
    return [e for r in rows if (e := get_event(r["id"]))]
