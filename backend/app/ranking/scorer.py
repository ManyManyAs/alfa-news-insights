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
OTHER_INDUSTRY_DISCOUNT = 0.5
# Региональная новость (субсидия, аренда, тариф в другом регионе) для компании и её контрагентов из других регионов —
# сигнал второго порядка. Москва не считается «чужим регионом»: в федеральных новостях она упоминается постоянно.
FOREIGN_REGION_DISCOUNT = 0.5
MACRO_FACTORS = {"key_rate", "inflation", "fx_rub", "credit_conditions", "labor_market", "budget_policy",
                 "tax_regulation", "geopolitics", "bond_market"}


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
    # Новость о чужой отрасли: макроканалы (ставка, курс, инфляция…) действуют на всех, а отраслевые факторы
    # («спрос», «стройка») описывают ту отрасль — их вклад ослабляем
    related = {(profile.okved or "")[:2]} | {m["okved2"] for m in profile.industry_mix if m["share"] >= 0.15}
    other_industry = bool(cls.industries) and not (set(cls.industries) & related)
    my_regions = {profile.region} | {r["region"] for r in profile.cp_regions if r["share"] >= 0.2}
    foreign_region = bool(cls.regions) and "Москва" not in cls.regions and not (set(cls.regions) & my_regions)
    seen: set[str] = set()
    for hit in cls.factors:
        if hit.factor in seen:  # старые разборы модели могли содержать повтор фактора
            continue
        seen.add(hit.factor)
        exp = exposures.get(hit.factor)
        if not exp or exp.weight <= 0:  # тема скрыта пользователем
            continue
        discount = OTHER_INDUSTRY_DISCOUNT if other_industry and hit.factor not in MACRO_FACTORS else 1.0
        if foreign_region:
            discount *= FOREIGN_REGION_DISCOUNT
        contrib = exp.weight * hit.confidence * discount
        total += contrib
        matched.append(hit.factor)
        eff = effect_of(exp, hit.direction)
        effects.append((contrib, eff))
        own = [r for r in exp.reasons if not r["basis"].startswith("user:")]
        specific = [r for r in own if r["basis"] != "baseline"]
        top_reason = max(specific or own or exp.reasons, key=lambda r: r["weight"])
        personal = next((r for r in exp.reasons if r["basis"].startswith("user:")), None)
        reasons.append({
            "specific": bool(specific),
            "factor": hit.factor, "factor_title": titles.get(hit.factor, hit.factor), "direction": hit.direction,
            "effect": eff, "contribution": round(contrib, 3), "basis": [r["basis"] for r in own],
            "personal": ({"why": personal["why"], "multiplier": personal["multiplier"]} if personal else None),
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
    total += _industry_region(cls, profile, reasons, has_factors=total > 0)
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


REGION_BONUS = 0.35
INDUSTRY_BONUS = 0.45


def _industry_region(cls, profile: CompanyProfile, reasons: list[dict], has_factors: bool) -> float:
    """Прямые совпадения: новость об отрасли компании / её регионе; косвенные — об отрасли или регионе контрагентов."""
    add = 0.0
    own2 = (profile.okved or "")[:2]
    if own2 and own2 in cls.industries:
        add += INDUSTRY_BONUS
        reasons.append({"factor": "industry", "factor_title": "Отрасль компании", "effect": "watch", "specific": True,
                        "contribution": INDUSTRY_BONUS, "basis": [f"profile:OKVED={own2}"],
                        "why": f"Новость об отрасли компании: {profile.okved_name or own2}"})
    else:
        for m in profile.industry_mix:
            if m["share"] >= 0.15 and m["okved2"] in cls.industries and m["okved2"] != own2:
                c = round(INDUSTRY_BONUS * min(1.0, m["share"] * 1.5) * 0.6, 3)
                role = "покупателей" if m["direction"] == "in" else "поставщиков"
                add += c
                reasons.append({"factor": "industry", "factor_title": "Отрасль контрагентов", "effect": "watch",
                                "specific": True, "contribution": c, "basis": [f"graph:{m['direction']}:{m['okved2']}"],
                                "why": f"Новость об отрасли «{m['name']}» — на неё приходится {m['share']:.0%} "
                                       f"платежей {role}",
                                "indirect": [{"basis": f"graph:{m['direction']}:{m['okved2']}",
                                              "why": f"{m['share']:.0%} платежей {role}"}]})
                break
    # регион — усилитель, а не самостоятельная причина: «Москва» в тексте не делает новость деловой
    if has_factors or add:
        if profile.region and profile.region in cls.regions:
            add += REGION_BONUS
            reasons.append({"factor": "region", "factor_title": "Регион компании", "effect": "watch", "specific": True,
                            "contribution": REGION_BONUS, "basis": ["profile:REGION"],
                            "why": f"Событие касается региона компании: {profile.region}"})
        else:
            for r in profile.cp_regions:
                if r["share"] >= 0.2 and r["region"] in cls.regions and r["region"] != profile.region:
                    c = round(REGION_BONUS * 0.5, 3)
                    add += c
                    role = "покупатели" if r["direction"] == "in" else "поставщики"
                    reasons.append({"factor": "region", "factor_title": "Регион контрагентов", "effect": "watch",
                                    "specific": True, "contribution": c, "basis": [f"graph:region:{r['region']}"],
                                    "why": f"В регионе «{r['region']}» находятся {role} компании ({r['share']:.0%} платежей)"})
                    break
    return add


def rank_feed(events: list[Event], profile: CompanyProfile, role: str | None = None, limit: int = 20,
              now: datetime | None = None, diversify: bool = True, personal: bool = True) -> list[Scored]:
    exposures = compute_exposures(profile, role)
    if personal:
        from app.ranking.personal import apply_overrides

        exposures = apply_overrides(profile.uid, exposures)
    scored = [score_event(e, profile, exposures, now) for e in events]
    scored = [s for s in scored if s.score >= MIN_SCORE * freshness(s.event.published_at, now) and s.reasons]
    scored.sort(key=lambda s: s.score, reverse=True)
    if not diversify:
        return collapse_stories(scored, limit)
    return mmr(collapse_stories(scored, limit * 3), limit)


MMR_LAMBDA = 0.3
NON_FACTOR = {"country", "industry", "region"}


def primary_factor(s: Scored) -> str | None:
    return next((r["factor"] for r in s.reasons if r["factor"] not in NON_FACTOR), None)


def _similarity(a: Scored, b: Scored) -> float:
    if primary_factor(a) and primary_factor(a) == primary_factor(b):
        return 1.0
    fa, fb = set(a.matched_factors), set(b.matched_factors)
    return 0.5 * len(fa & fb) / len(fa | fb) if fa and fb else 0.0


def mmr(cands: list[Scored], limit: int, lam: float = MMR_LAMBDA) -> list[Scored]:
    """Maximal Marginal Relevance: следующая карточка — самая релевантная с поправкой на похожесть на уже выбранные.

    Без этого лента компании с кредитом — пять новостей про ставку подряд; с MMR вторая новость про ставку
    уступает место риску по другому каналу, если он почти так же важен.
    """
    pool, out = list(cands), []
    while pool and len(out) < limit:
        best = max(pool, key=lambda c: c.score * (1 - lam * max((_similarity(c, x) for x in out), default=0.0)))
        pool.remove(best)
        out.append(best)
    return out


def collapse_stories(scored: list[Scored], limit: int) -> list[Scored]:
    """Один сюжет — одна карточка: оставляем самое релевантное событие сюжета, остальные доступны в таймлайне."""
    out, seen = [], set()
    for s in scored:
        sid = s.event.story_id
        if sid:
            if sid in seen:
                continue
            seen.add(sid)
        out.append(s)
        if len(out) >= limit:
            break
    return out
