"""Матрица чувствительности: профиль компании -> веса факторов с обоснованиями."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import load_yaml
from app.data.models import CompanyProfile


@dataclass
class Exposure:
    factor: str
    weight: float = 0.0
    sign: int = 0  # знак влияния роста фактора; 0 — неоднозначно
    reasons: list[dict] = field(default_factory=list)  # [{"basis": "profile:CL_PRODUCT_LOA_MARK", "why": "..."}]


def _okved_rules(code: str, rules: dict) -> list[tuple[str, list[dict]]]:
    """Все подходящие префиксы ОКВЭД (от общего к частному)."""
    matched = [(p, r) for p, r in rules.items() if code == p or code.startswith(p + ".")]
    return sorted(matched, key=lambda x: len(x[0]))


def compute_exposures(profile: CompanyProfile, role: str | None = None) -> dict[str, Exposure]:
    cfg = load_yaml("sensitivity.yaml")
    exp: dict[str, Exposure] = {}

    def add(factor: str, weight: float, sign: int, basis: str, why: str) -> None:
        e = exp.setdefault(factor, Exposure(factor=factor))
        # noisy-OR: несколько признаков усиливают чувствительность, но вес не растёт без предела
        e.weight = 1 - (1 - e.weight) * (1 - min(max(weight, 0.0), 1.0))
        if sign and not e.sign:
            e.sign = sign
        elif sign and e.sign and sign != e.sign:
            e.sign = 0  # противоположные эффекты — неоднозначно, решит агент
        e.reasons.append({"basis": basis, "why": why, "weight": weight})

    for factor, w in (cfg.get("baseline") or {}).items():
        add(factor, w, 0, "baseline", "Макрофактор, важный для любого бизнеса")

    for product in profile.products:
        for rule in (cfg["products"].get(product) or {}).get("exposures", []):
            add(rule["factor"], rule["weight"], rule.get("sign", 0), f"profile:{product}", rule["why"])

    for country in profile.fts_countries:
        for rule in cfg.get("countries", {}).get(country.upper(), []):
            add(rule["factor"], rule["weight"], rule.get("sign", 0), f"profile:FTS_COUNTRY={country}", rule["why"])

    for prefix, rules in _okved_rules(profile.okved, cfg.get("okved", {})):
        for rule in rules:
            add(rule["factor"], rule["weight"], rule.get("sign", 0), f"profile:OKVED={prefix}", f"Отрасль: {rule['why']}")

    # Эффекты второго порядка: отраслевой профиль ВСЕХ контрагентов (доли платежей по классам ОКВЭД).
    # Своя отрасль пропускается — она уже учтена напрямую.
    own2 = profile.okved[:2]
    for m in profile.industry_mix:
        if m["share"] < 0.15 or m["okved2"] == own2:
            continue
        role_txt = "покупателей" if m["direction"] == "in" else "поставщиков"
        for _prefix, rules in _okved_rules(m["okved2"], cfg.get("okved", {})):
            for rule in rules:
                add(rule["factor"], 0.45 * m["share"] * rule["weight"], 0, f"graph:{m['direction']}:{m['okved2']}",
                    f"{m['share']:.0%} платежей {role_txt} приходится на отрасль «{m['name']}» — "
                    f"событие может дойти до компании через них")

    # Холодный старт: у компании мало признаков (нет продуктов кроме РКО, нет ВЭД и платежей) —
    # добавляем слабую чувствительность по продуктам, типичным для похожих компаний.
    signals = [x for x in profile.products if x != "CL_PRODUCT_RKO_MARK"]
    if not signals and not profile.industry_mix:
        from app.data.peers import PRODUCT_TITLES, peer_stats

        st = peer_stats(profile.peer_key)
        for product, share in ((st or {}).get("products") or {}).items():
            if share < 0.3 or product == "CL_PRODUCT_RKO_MARK":
                continue
            for rule in (cfg["products"].get(product) or {}).get("exposures", []):
                add(rule["factor"], 0.5 * share * rule["weight"], rule.get("sign", 0), f"peers:{product}",
                    f"Данных о компании мало; у {share:.0%} похожих компаний есть {PRODUCT_TITLES[product]} — "
                    f"{rule['why'][0].lower() + rule['why'][1:]}")

    if role:
        mult = (cfg.get("roles", {}).get(role) or {}).get("multipliers", {})
        for factor, m in mult.items():
            if factor in exp:
                exp[factor].weight *= m
    return exp


def role_titles() -> dict[str, str]:
    return {k: v["title"] for k, v in load_yaml("sensitivity.yaml").get("roles", {}).items()}
