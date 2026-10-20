"""Персональное ранжирование событий (детерминированно и объяснимо).

score = Σ_f  вес_фактора_в_профиле(f, роль) × уверенность_классификации(f)
        + совпадение стран ВЭД + важность события
        × свежесть
Каждое слагаемое сохраняется как причина — это и есть «почему вам это показано».
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import load_yaml
from app.data.models import CompanyProfile
from app.data.sensitivity import Exposure, compute_exposures
from app.news.models import Event

DIR = {"up": 1, "down": -1, "stable": 0, "unknown": 0}
HALF_LIFE_DAYS = 14
MIN_SCORE = 0.35


@dataclass
class Scored:
    event: Event
    score: float
    effect: str  # risk | opportunity | mixed | watch
    reasons: list[dict] = field(default_factory=list)
    matched_factors: list[str] = field(default_factory=list)


# Факторы, где «без изменений» — тоже сигнал: рынок ждал снижения ставки, пауза = деньги дорогие дольше
STABLE_IS_SIGNAL = {"key_rate": -1}


def effect_of(exp: Exposure, direction: str) -> str:
    d = DIR.get(direction, 0)
    if direction == "stable" and exp.factor in STABLE_IS_SIGNAL and exp.sign:
        d = STABLE_IS_SIGNAL[exp.factor] * -1  # пауза в снижении ставки эквивалентна «ставка выше ожиданий»
        return "opportunity" if d * exp.sign > 0 else "risk"
    if direction == "stable" and exp.sign:
        return "watch"
    if not d or not exp.sign:
        return "mixed" if d else "watch"
    return "opportunity" if d * exp.sign > 0 else "risk"


def freshness(published_at: str | None, now: datetime | None = None) -> float:
    if not published_at:
        return 0.7
    try:
        t = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.7
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    days = max(0.0, (now - t).total_seconds() / 86400)
    return max(0.25, math.pow(0.5, days / HALF_LIFE_DAYS))


def score_event(event: Event, profile: CompanyProfile, exposures: dict[str, Exposure],
                now: datetime | None = None) -> Scored:
    titles = {k: v["title"] for k, v in load_yaml("factors.yaml")["factors"].items()}
    cls = event.classification
    total, reasons, matched, effects = 0.0, [], [], []
    for hit in cls.factors:
        exp = exposures.get(hit.factor)
        if not exp:
            continue
        contrib = exp.weight * hit.confidence
        total += contrib
        matched.append(hit.factor)
        eff = effect_of(exp, hit.direction)
        effects.append((contrib, eff))
        top_reason = max([r for r in exp.reasons if r["basis"] != "baseline"] or exp.reasons, key=lambda r: r["weight"])
        specific = [r for r in exp.reasons if r["basis"] != "baseline"]
        reasons.append({
            "specific": bool(specific),
            "factor": hit.factor, "factor_title": titles.get(hit.factor, hit.factor), "direction": hit.direction,
            "effect": eff, "contribution": round(contrib, 3), "basis": [r["basis"] for r in exp.reasons],
            "why": top_reason["why"], "evidence": hit.evidence,
            # каналы через контрагентов и похожие компании (эффекты второго порядка, холодный старт)
            "indirect": [{"basis": r["basis"], "why": r["why"]} for r in exp.reasons
                         if r["basis"].startswith(("graph:", "peers:"))][:2],
        })
    countries = {c.upper() for c in profile.fts_countries}
    common = [c for c in cls.countries if c.upper() in countries]
    if common:
        total += 0.5
        reasons.append({"factor": "country", "factor_title": "Страны ВЭД", "effect": "watch", "contribution": 0.5,
                        "basis": ["profile:FTS_COUNTRY_NAME"], "why": f"Компания ведёт ВЭД с: {', '.join(common)}"})
    if total > 0:
        total += 0.08 * cls.importance
    total *= freshness(event.published_at, now)
    effect = "watch"
    if effects:
        risk = sum(c for c, e in effects if e == "risk")
        opp = sum(c for c, e in effects if e == "opportunity")
        if risk and opp and min(risk, opp) / max(risk, opp) > 0.5:
            effect = "mixed"
        elif risk > opp:
            effect = "risk"
        elif opp > risk:
            effect = "opportunity"
    reasons.sort(key=lambda r: r["contribution"], reverse=True)
    return Scored(event=event, score=round(total, 3), effect=effect, reasons=reasons, matched_factors=matched)


def rank_feed(events: list[Event], profile: CompanyProfile, role: str | None = None, limit: int = 20,
              now: datetime | None = None) -> list[Scored]:
    exposures = compute_exposures(profile, role)
    scored = [score_event(e, profile, exposures, now) for e in events]
    scored = [s for s in scored if s.score >= MIN_SCORE * freshness(s.event.published_at, now) and s.reasons]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:limit]
