"""Персональные поправки к весам факторов компании.

Два источника:
- обратная связь: 👎 «не про мой бизнес» ослабляет главные факторы события (×0,6, не ниже 0,2),
  👍 усиливает (×1,2, не выше 2,0) — лента «учится» без переобучения модели;
- ручная настройка в профиле: «важнее» (×1,6) или «не показывать» (×0). Ручная настройка главнее обратной связи.

Поправка всегда видна: в причинах карточки и в профиле, её можно сбросить.
"""
from __future__ import annotations

from app.config import load_yaml
from app.data.sensitivity import Exposure
from app.news.models import Event
from app.storage import db

DOWN, UP, FLOOR, CEIL = 0.6, 1.2, 0.2, 2.0
LEVELS = {"more": 1.6, "mute": 0.0}
# причины 👎, которые означают «тема не для меня»; «уже знал» и «неточный вывод» на веса не влияют
NOT_RELEVANT = {"not_relevant"}


def get_overrides(uid: str) -> dict[str, dict]:
    return {r["factor"]: dict(r) for r in db.query("SELECT * FROM profile_overrides WHERE company_uid=?", (uid,))}


def _set(uid: str, factor: str, mult: float, source: str, note: str) -> None:
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO profile_overrides(company_uid, factor, multiplier, source, note, updated_at)"
                     " VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)", (uid, factor, mult, source, note))


def apply_feedback(uid: str, event: Event, matched: list[str], rating: int, reason: str | None) -> list[dict]:
    """Обновляет поправки по главным факторам события; возвращает, что изменилось (для подсказки в UI)."""
    if rating < 0 and reason not in NOT_RELEVANT:
        return []
    titles = {k: v["title"] for k, v in load_yaml("factors.yaml")["factors"].items()}
    cur = get_overrides(uid)
    changed = []
    factors = [f for f in matched if f in titles][:2] or [h.factor for h in event.classification.factors[:1]]
    for f in factors:
        o = cur.get(f)
        if o and o["source"] == "manual":
            continue
        m = o["multiplier"] if o else 1.0
        m = max(FLOOR, m * DOWN) if rating < 0 else min(CEIL, m * UP)
        note = "Вы отметили похожие новости как неактуальные" if rating < 0 else "Вы отметили похожие новости как полезные"
        _set(uid, f, round(m, 3), "feedback", note)
        changed.append({"factor": f, "title": titles.get(f, f), "multiplier": round(m, 3)})
    return changed


def set_manual(uid: str, factor: str, level: str) -> None:
    if level == "normal":
        with db.tx() as conn:
            conn.execute("DELETE FROM profile_overrides WHERE company_uid=? AND factor=?", (uid, factor))
        return
    note = {"more": "Вы попросили показывать такие новости чаще", "mute": "Вы скрыли новости по этой теме"}[level]
    _set(uid, factor, LEVELS[level], "manual", note)


def reset(uid: str) -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM profile_overrides WHERE company_uid=?", (uid,))
        conn.execute("DELETE FROM watchlist WHERE company_uid=?", (uid,))


# ------------------------------------------------------------------ «Слежу за»
WATCH_KINDS = ("region", "country", "industry", "topic")
WATCH_MAX = 12


def get_watchlist(uid: str) -> list[dict]:
    return [dict(r) for r in db.query("SELECT kind, value, title FROM watchlist WHERE company_uid=? ORDER BY created_at", (uid,))]


def add_watch(uid: str, kind: str, value: str, title: str) -> list[dict]:
    if kind not in WATCH_KINDS:
        raise ValueError("Неизвестный тип")
    if len(get_watchlist(uid)) >= WATCH_MAX:
        raise ValueError(f"Не больше {WATCH_MAX} пунктов в списке «Слежу за»")
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO watchlist(company_uid, kind, value, title) VALUES (?,?,?,?)",
                     (uid, kind, value, title))
    return get_watchlist(uid)


def remove_watch(uid: str, kind: str, value: str) -> list[dict]:
    with db.tx() as conn:
        conn.execute("DELETE FROM watchlist WHERE company_uid=? AND kind=? AND value=?", (uid, kind, value))
    return get_watchlist(uid)


HIDE = 0.8  # скрытая карточка — мягкий сигнал: тема чуть ниже (сильнее — только «не про мой бизнес»)


def apply_hide(uid: str, event: Event, matched: list[str], hidden: bool) -> list[dict]:
    """Скрытие карточки ослабляет её главную тему (×0,8, не ниже 0,2); возврат в ленту — отменяет."""
    titles = {k: v["title"] for k, v in load_yaml("factors.yaml")["factors"].items()}
    f = next((x for x in matched if x in titles), None) or next(
        (h.factor for h in event.classification.factors if h.factor in titles), None)
    if not f:
        return []
    o = get_overrides(uid).get(f)
    if o and o["source"] == "manual":
        return []
    m = o["multiplier"] if o else 1.0
    m = max(FLOOR, m * HIDE) if hidden else min(1.0, m / HIDE)
    if not hidden and m >= 0.999 and (not o or o["source"] == "hide"):
        with db.tx() as conn:
            conn.execute("DELETE FROM profile_overrides WHERE company_uid=? AND factor=?", (uid, f))
        return [{"factor": f, "title": titles[f], "multiplier": 1.0}]
    _set(uid, f, round(m, 3), "hide" if not o or o["source"] == "hide" else o["source"],
         "Вы скрыли похожие новости" if not o or o["source"] == "hide" else o["note"])
    return [{"factor": f, "title": titles[f], "multiplier": round(m, 3)}]


def apply_overrides(uid: str, exposures: dict[str, Exposure]) -> dict[str, Exposure]:
    for f, o in get_overrides(uid).items():
        e = exposures.get(f)
        if not e:
            if o["multiplier"] <= 1:
                continue
            e = exposures[f] = Exposure(factor=f, weight=0.3)  # «важнее» для темы, которой нет в профиле
        e.weight *= o["multiplier"]
        e.reasons.append({"basis": f"user:{o['source']}", "why": o["note"], "weight": 0.0, "multiplier": o["multiplier"]})
    return exposures
