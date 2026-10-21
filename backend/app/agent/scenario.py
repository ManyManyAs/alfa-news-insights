"""Сценарии и «что если» — детерминированный калькулятор без LLM.

Модель не придумывает числа в деньгах: их считает этот модуль по прозрачным формулам. Базовый сценарий —
прогноз Альфа-Банка (ставка, курс, зарплаты); негативный и позитивный — сдвиги от него. Пользователь может
ввести свои суммы (долг, импорт, ФОТ) — тогда оценка перестаёт быть оценкой по профилю.

Формулы (годовой эффект на прибыль до налога, ₽; минус — потери):
  ставка:   −долг × доля_плавающей_ставки × Δставки + депозиты × Δставки
  курс:     −импорт × Δкурса × (1 − перенос_в_цены) + экспорт × Δкурса
  зарплаты: −ФОТ × (рост_зарплат − заложенный_в_бюджет_рост)
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.data.models import CompanyProfile
from app.knowledge.bank import get_knowledge

AVG_MONTHLY_WAGE = 95_000  # ₽, оценка средней зарплаты для ФОТ по численности (явно помечается как оценка)
PAYROLL_TAXES = 1.3  # страховые взносы
RATE_FACTORS = {"key_rate", "credit_conditions", "bond_market"}
FX_FACTORS = {"fx_rub", "china_trade", "sanctions_trade", "commodity_oil"}
WAGE_FACTORS = {"labor_market"}


class Inputs(BaseModel):
    """Параметры компании и шоки. None — взять оценку по профилю."""
    revenue_rub: float | None = Field(None, ge=0)
    debt_rub: float | None = Field(None, ge=0)
    floating_share: float | None = Field(None, ge=0, le=1)
    deposits_rub: float | None = Field(None, ge=0)
    import_rub: float | None = Field(None, ge=0)
    export_rub: float | None = Field(None, ge=0)
    pass_through: float | None = Field(None, ge=0, le=1)
    payroll_rub: float | None = Field(None, ge=0)
    planned_wage_growth_pct: float | None = Field(None, ge=-20, le=50)
    rate_delta_pp: float = Field(0.0, ge=-10, le=10)
    fx_change_pct: float = Field(0.0, ge=-50, le=100)
    wage_growth_pct: float | None = Field(None, ge=-20, le=50)


def _workers_mid(band: str | None) -> float | None:
    nums = [int(x) for x in re.findall(r"\d+", band or "")]
    if not nums:
        return None
    return (nums[0] + nums[1]) / 2 if len(nums) > 1 else nums[0] * 1.2


def bank_baseline() -> dict:
    """Базовый сценарий = прогноз банка на горизонте 12 месяцев."""
    kb = get_knowledge()
    pts = (kb.rate_path or {}).get("points") or []
    current = pts[0][1] if pts else None
    avg27 = (kb.forecast("key_rate_avg", "2027") or {}).get("value")
    wages = kb.forecast("wages_nominal", "2026") or {}
    usd26, usd27 = kb.forecast("usd_rub", "2026") or {}, kb.forecast("usd_rub", "2027") or {}
    fx_change = round((usd27["value"] / usd26["value"] - 1) * 100, 1) if usd26 and usd27 else 0.0
    return {
        "key_rate_now": current, "key_rate_avg_next": avg27,
        "rate_delta_pp": round(avg27 - current, 1) if current is not None and avg27 is not None else 0.0,
        "fx_change_pct": fx_change, "usd_rub": [usd26.get("value"), usd27.get("value")],
        "wage_growth_pct": wages.get("value"),
        "refs": {"rate": "bank:fc-key_rate_avg-2027" if avg27 is not None else None, "fx": usd27.get("ref"),
                 "wages": wages.get("ref")},
        "doc_date": (kb.rate_path or {}).get("date"),
    }


def defaults(p: CompanyProfile) -> tuple[dict, list[str]]:
    """Оценки параметров компании по профилю + список того, что именно оценено (для честной подписи в UI)."""
    est: list[str] = []
    rev = None if p.revenue_suspicious else p.revenue
    has_loan = "CL_PRODUCT_LOA_MARK" in p.products or "CL_PRODUCT_LEA_MARK" in p.products
    d: dict = {"revenue_rub": rev}
    if rev:
        if has_loan:
            d["debt_rub"], d["floating_share"] = round(rev * 0.25), 0.5
            est.append("долг ≈ 25% выручки, половина — по плавающей ставке")
        else:
            d["debt_rub"], d["floating_share"] = 0, 0.5
        if "CL_PRODUCT_DEP_MARK" in p.products:
            d["deposits_rub"] = round(rev * 0.05)
            est.append("депозиты ≈ 5% выручки")
        if p.fts:
            d["import_rub"] = round(rev * 0.3)
            est.append("импорт ≈ 30% выручки")
        d["pass_through"] = 0.3
    mid = _workers_mid(p.workers)
    if mid:
        d["payroll_rub"] = round(mid * AVG_MONTHLY_WAGE * 12 * PAYROLL_TAXES)
        est.append(f"ФОТ по численности {p.workers} чел. и средней зарплате {AVG_MONTHLY_WAGE // 1000} тыс. ₽")
    return d, est


def calc(p: CompanyProfile, inp: Inputs) -> dict:
    base = bank_baseline()
    d, est = defaults(p)
    v = {k: (getattr(inp, k) if getattr(inp, k) is not None else d.get(k)) for k in
         ("revenue_rub", "debt_rub", "floating_share", "deposits_rub", "import_rub", "export_rub", "pass_through",
          "payroll_rub", "planned_wage_growth_pct")}
    user_set = [k for k in v if getattr(inp, k) is not None]
    planned = v["planned_wage_growth_pct"] if v["planned_wage_growth_pct"] is not None else base["wage_growth_pct"] or 0
    wage = inp.wage_growth_pct if inp.wage_growth_pct is not None else planned
    rows = []
    if v["debt_rub"] or v["deposits_rub"]:
        debt = -(v["debt_rub"] or 0) * (v["floating_share"] or 0) * inp.rate_delta_pp / 100
        dep = (v["deposits_rub"] or 0) * inp.rate_delta_pp / 100
        rows.append({"lever": "rate", "title": "Ставка", "effect_rub": round(debt + dep),
                     "formula": f"−долг × доля под плавающей ставкой × ({num(inp.rate_delta_pp)} п.п.)"
                                + (" + доход по депозитам" if dep else "")})
    if v["import_rub"] or v["export_rub"]:
        imp = -(v["import_rub"] or 0) * inp.fx_change_pct / 100 * (1 - (v["pass_through"] or 0))
        exp = (v["export_rub"] or 0) * inp.fx_change_pct / 100
        rows.append({"lever": "fx", "title": "Курс", "effect_rub": round(imp + exp),
                     "formula": f"−импорт × ({num(inp.fx_change_pct, 0)}%) × (1 − доля, переносимая в цены)"
                                + (" + выручка экспорта" if exp else "")})
    if v["payroll_rub"]:
        rows.append({"lever": "wages", "title": "Зарплаты", "effect_rub": round(-v["payroll_rub"] * (wage - planned) / 100),
                     "formula": f"−ФОТ × (рост {num(wage)}% − {num(planned)}% заложено в бюджет по прогнозу банка)"})
    total = sum(r["effect_rub"] for r in rows)
    rev = v["revenue_rub"]
    return {"rows": rows, "total_rub": round(total), "share_of_revenue": round(total / rev, 4) if rev else None,
            "values": v, "estimated": est, "user_set": user_set, "baseline": base}


def relevant_levers(factors: list[str]) -> list[str]:
    fs = set(factors)
    out = [lv for lv, group in (("rate", RATE_FACTORS), ("fx", FX_FACTORS), ("wages", WAGE_FACTORS)) if fs & group]
    return out or ["rate"]


def scenarios(p: CompanyProfile, factors: list[str], user: Inputs | None = None) -> list[dict]:
    """Три сценария на 12 месяцев: базовый (прогноз банка), негативный и позитивный — с триггерами."""
    base = bank_baseline()
    levers = relevant_levers(factors)
    u = (user or Inputs()).model_dump()
    plan = {
        "base": {"rate_delta_pp": base["rate_delta_pp"], "fx_change_pct": base["fx_change_pct"],
                 "wage_growth_pct": base["wage_growth_pct"]},
        "negative": {"rate_delta_pp": base["rate_delta_pp"] + 2, "fx_change_pct": base["fx_change_pct"] + 15,
                     "wage_growth_pct": (base["wage_growth_pct"] or 0) + 5},
        "positive": {"rate_delta_pp": base["rate_delta_pp"] - 1, "fx_change_pct": base["fx_change_pct"] - 5,
                     "wage_growth_pct": (base["wage_growth_pct"] or 0) - 3},
    }
    titles = {"base": "Базовый — прогноз Альфа-Банка", "negative": "Негативный", "positive": "Позитивный"}
    out = []
    for name, shock in plan.items():
        params = {k: v for k, v in shock.items() if (k.startswith("rate") and "rate" in levers)
                  or (k.startswith("fx") and "fx" in levers) or (k.startswith("wage") and "wages" in levers)}
        inp = Inputs(**{**{k: v for k, v in u.items() if v is not None and k not in shock}, **params})
        res = calc(p, inp)
        out.append({"name": name, "title": titles[name], "params": params, "assumptions": _assumptions(params, base),
                    "total_rub": res["total_rub"], "share_of_revenue": res["share_of_revenue"], "rows": res["rows"],
                    "estimated": res["estimated"],
                    "sources": [r for lv in levers if (r := base["refs"].get(lv))] if name == "base" else ["calc:scenario"]})
    return out


def _assumptions(params: dict, base: dict) -> str:
    parts = []
    if "rate_delta_pp" in params and base["key_rate_now"] is not None:
        parts.append(f"средняя ключевая ставка {num(base['key_rate_now'] + params['rate_delta_pp'])}% "
                     f"(сейчас {num(base['key_rate_now'])}%)")
    if "fx_change_pct" in params:
        parts.append(f"рубль {'слабее' if params['fx_change_pct'] > 0 else 'крепче'} на {abs(params['fx_change_pct']):.0f}%")
    if params.get("wage_growth_pct") is not None:
        parts.append(f"зарплаты +{num(params['wage_growth_pct'])}% за год")
    return "; ".join(parts)


def money(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    s = f"{num(a / 1e9)} млрд ₽" if a >= 1e9 else f"{num(a / 1e6)} млн ₽" if a >= 1e6 else f"{a / 1e3:.0f} тыс. ₽"
    return ("−" if v < 0 else "+" if v > 0 else "") + s


def num(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def ru_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def triggers(p: CompanyProfile, factors: list[str], effective_dates: list[str] | None = None) -> list[dict]:
    """«Что может изменить вывод» — конкретные события с числами банка как точкой отсчёта.

    Возвращает [{"text", "source"}]: source — прогноз банка, на который опирается триггер, или "news" для даты.
    """
    kb = get_knowledge()
    base = bank_baseline()
    out = []
    levers = relevant_levers(factors)
    ref = lambda ind, per: (kb.forecast(ind, per) or {}).get("ref")  # noqa: E731
    if "rate" in levers and base["key_rate_avg_next"] is not None:
        out.append({"text": f"Банк России снижает ставку быстрее прогноза Альфа-Банка (средняя {num(base['key_rate_avg_next'])}% "
                            f"в 2027 г.) — долговая нагрузка падает, вывод смягчается", "source": ref("key_rate_avg", "2027")})
        out.append({"text": "Пауза в снижении ставки или её повышение — процентные расходы растут, риск усиливается",
                    "source": ref("key_rate_end", "2026") or ref("key_rate_avg", "2027")})
    if "fx" in levers and base["usd_rub"][0]:
        out.append({"text": f"Курс заметно слабее прогноза банка ({num(base['usd_rub'][0])} ₽/$ на конец 2026 г.) — "
                            f"дорожает импорт и валютные платежи", "source": ref("usd_rub", "2026")})
    if "wages" in levers and base["wage_growth_pct"]:
        out.append({"text": f"Рост зарплат выше прогноза банка ({num(base['wage_growth_pct'])}% в 2026 г.) — давление на ФОТ",
                    "source": ref("wages_nominal", "2026")})
    for d in (effective_dates or [])[:1]:
        out.append({"text": f"Изменение параметров нормы до вступления в силу ({ru_date(d)}) — вывод нужно пересчитать",
                    "source": "news"})
    return out[:4]
