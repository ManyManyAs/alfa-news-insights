"""Онлайн-метрики продукта и алерты (этап 9).

События интерфейса (просмотр главной, клик по виджету, открытие разбора, переход к первоисточнику, вопрос в чате,
«что если», время до первого вывода…) пишутся в ui_events. Отсюда — воронка, вовлечённость, скорость и стоимость.
Персональные данные не нужны: метрики агрегированы, в событиях — только тип, событие ленты и число.
"""
from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime, timedelta

from app.config import REPO_ROOT
from app.storage import db

ALLOWED = {
    "home_view", "widget_click", "feed_view", "card_open", "preview_ready", "insight_ready", "source_click",
    "action_open", "chat_question", "whatif_open", "digest_view", "tour_start", "tour_finish", "map_hover",
    "js_error",  # ошибка в браузере: только текст ошибки и файл:строка, без данных пользователя
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT, company_uid TEXT, type TEXT, event_id TEXT, value REAL, meta TEXT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ui_events_ts ON ui_events(ts, type);
"""


def ensure_schema() -> None:
    db.get_conn().executescript(SCHEMA)


def track(user: str, events: list[dict], allowed_companies: set[str]) -> int:
    ensure_schema()
    rows = []
    for e in events[:50]:
        t = e.get("type")
        if t not in ALLOWED:
            continue
        cid = e.get("company_id")
        if cid and cid not in allowed_companies:  # чужую компанию в метрики не пишем
            continue
        value = e.get("value")
        rows.append((user, cid, t, (e.get("event_id") or None) and str(e["event_id"])[:64],
                     float(value) if isinstance(value, int | float) else None,
                     json.dumps(e.get("meta") or {}, ensure_ascii=False)[:300]))
    with db.tx() as conn:
        conn.executemany("INSERT INTO ui_events(user_id, company_uid, type, event_id, value, meta) VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def _pct(values: list[float], q: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0])
    return round(statistics.quantiles(values, n=100)[q - 1])


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def product_metrics(days: int = 7) -> dict:
    ensure_schema()
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cnt = {r["type"]: r["n"] for r in db.query("SELECT type, COUNT(*) n FROM ui_events WHERE ts >= ? GROUP BY type", (since,))}
    users = db.query_one("SELECT COUNT(DISTINCT user_id) n FROM ui_events WHERE ts >= ?", (since,))["n"]
    vals = lambda t, cached=None: [r["value"] for r in db.query(  # noqa: E731
        "SELECT value, meta FROM ui_events WHERE type=? AND ts >= ? AND value IS NOT NULL", (t, since))
        if cached is None or json.loads(r["meta"] or "{}").get("cached") is cached]
    opened_from = {r["src"]: r["n"] for r in db.query(
        "SELECT json_extract(meta, '$.from') src, COUNT(*) n FROM ui_events WHERE type='card_open' AND ts >= ? GROUP BY src", (since,))}
    fb = db.query_one("SELECT COALESCE(SUM(rating>0),0) pos, COALESCE(SUM(rating<0),0) neg FROM feedback WHERE ts >= ?", (since,))
    reasons = {r["reason"] or "без причины": r["n"] for r in db.query(
        "SELECT reason, COUNT(*) n FROM feedback WHERE rating<0 AND ts >= ? GROUP BY reason", (since,))}
    actions = {r["kind"]: r["n"] for r in db.query("SELECT kind, COUNT(*) n FROM action_requests WHERE ts >= ? GROUP BY kind", (since,))}
    hides = db.query_one("SELECT COUNT(*) n FROM hidden_events WHERE ts >= ?", (since,))["n"]
    opens = cnt.get("card_open", 0)
    preview, full_fresh = vals("preview_ready"), vals("insight_ready", cached=False)
    return {
        "days": days, "active_users": users, "counts": cnt,
        "funnel": {"home_views": cnt.get("home_view", 0), "widget_clicks": cnt.get("widget_click", 0),
                   "feed_views": cnt.get("feed_view", 0), "card_opens": opens, "opened_from": opened_from},
        "rates": {
            "widget_ctr": _ratio(cnt.get("widget_click", 0), cnt.get("home_view", 0)),
            "feed_open_rate": _ratio(opened_from.get("feed", 0), cnt.get("feed_view", 0)),
            "source_ctr": _ratio(cnt.get("source_click", 0), opens),
            "chat_per_insight": _ratio(cnt.get("chat_question", 0), opens),
            "whatif_rate": _ratio(cnt.get("whatif_open", 0), opens),
            "action_rate": _ratio(sum(actions.values()), opens),
            "hide_rate": _ratio(hides, opens),
            "helpful_share": _ratio(fb["pos"], fb["pos"] + fb["neg"]),
            "tour_completion": _ratio(cnt.get("tour_finish", 0), cnt.get("tour_start", 0)),
        },
        "feedback": {"pos": fb["pos"], "neg": fb["neg"], "neg_reasons": reasons},
        "actions": actions,
        "speed_ms": {"preview_p50": _pct(preview, 50), "preview_p95": _pct(preview, 95),
                     "full_fresh_p50": _pct(full_fresh, 50), "full_fresh_p95": _pct(full_fresh, 95),
                     "full_cached_p50": _pct(vals("insight_ready", cached=True), 50)},
    }


def cost_per_insight() -> dict:
    """Стоимость разбора по трассировкам: сколько рублей и вызовов модели уходит на один персональный разбор."""
    rows = db.query("SELECT t.trace_id, SUM(t.cost_rub) c, SUM(t.kind='llm') calls, SUM(t.duration_ms) ms "
                    "FROM trace_steps t JOIN insights i ON i.trace_id = t.trace_id GROUP BY t.trace_id")
    llm = [r for r in rows if (r["c"] or 0) > 0]
    return {"insights": len(rows), "with_llm": len(llm),
            "avg_rub": round(statistics.mean(r["c"] for r in llm), 3) if llm else None,
            "p95_rub": round(statistics.quantiles([r["c"] for r in llm], n=20)[18], 3) if len(llm) > 2 else None,
            "avg_llm_calls": round(statistics.mean(r["calls"] for r in llm), 1) if llm else None,
            "avg_build_s": round(statistics.mean(r["ms"] for r in llm) / 1000, 1) if llm else None}


def offline_metrics() -> dict | None:
    """Последняя офлайн-оценка: базовая линия из репозитория (обновляется вместе с кодом через PR)."""
    p = REPO_ROOT / "docs" / "quality" / "baseline.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def alerts(budget: dict, product: dict, judge: dict, sources_down: int, injections_24h: int) -> list[dict]:
    out = []

    def add(level: str, text: str) -> None:
        out.append({"level": level, "text": text})

    if budget.get("budget_rub"):
        share = budget["spent_rub"] / budget["budget_rub"]
        if share >= 0.8:
            add("warn", f"Израсходовано {share:.0%} дневного бюджета LLM — фоновые задачи приостановлены, остаток для пользователей")
    fb = product["feedback"]
    if fb["pos"] + fb["neg"] >= 5 and fb["neg"] / (fb["pos"] + fb["neg"]) > 0.2:
        add("warn", f"Доля 👎 {fb['neg'] / (fb['pos'] + fb['neg']):.0%} — выше порога 20%: разобрать причины")
    p95 = product["speed_ms"]["preview_p95"]
    if p95 is not None and p95 > 2000:
        add("warn", f"Первый вывод p95 = {p95} мс — дольше цели 2 с")
    js = product.get("counts", {}).get("js_error", 0)
    if js >= 5:
        add("warn", f"Ошибок в браузере за период: {js} — смотреть журнал ui_events (type=js_error)")
    if sources_down:
        add("warn", f"Источников новостей с ошибками: {sources_down}")
    if injections_24h > 5:
        add("warn", f"Всплеск подозрений на prompt injection за сутки: {injections_24h} — все перехвачены фильтром, проверить источники")
    if judge.get("avg") is not None and judge["avg"] < 4.2:
        add("info", f"Оценка LLM-судьи {str(judge['avg']).replace('.', ',')} — ниже цели 4,2")
    return out
