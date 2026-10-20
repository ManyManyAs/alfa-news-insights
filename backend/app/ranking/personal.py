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
