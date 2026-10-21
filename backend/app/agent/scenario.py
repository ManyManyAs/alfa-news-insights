"""Сценарии и «что если» — детерминированный калькулятор без LLM.

Модель не придумывает числа в деньгах: их считает этот модуль по прозрачным формулам. Базовый сценарий —
прогноз Альфа-Банка (ставка, курс, зарплаты); негативный и позитивный — сдвиги от него. Пользователь может
ввести свои суммы (долг, импорт, ФОТ) — тогда оценка перестаёт быть оценкой по профилю.

Формулы (эффект на прибыль до налога за 12 месяцев, ₽; минус — потери):
  ставка:   −долг × доля_плавающей_ставки × Δставки + депозиты × Δставки
  курс:     −импорт × Δкурса × (1 − перенос_в_цены) + экспорт × Δкурса
  зарплаты: −ФОТ × (рост_зарплат − заложенный_в_бюджет_рост)
  спрос:    выручка × доля, зависящая от фактора × Δспроса × маржинальность
  затраты:  −закупки × доля поставщиков из затронутой отрасли × Δцен × (1 − перенос_в_цены)
  налоги:   −выручка × Δналоговой_нагрузки (п.п. от выручки)
Отраслевые рычаги (спрос, затраты, налоги) берутся из того, ЧЕМ событие связано с компанией: прямая отрасль,
покупатели или поставщики в графе платежей. Размер шока — из новости («продажи −5%»), иначе — типовой для фактора.
Если норма вступает в силу позже, эффект считается только за месяцы после вступления.
"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from app.data.models import CompanyProfile
from app.knowledge.bank import get_knowledge

AVG_MONTHLY_WAGE = 95_000  # ₽, оценка средней зарплаты для ФОТ по численности (явно помечается как оценка)
PAYROLL_TAXES = 1.3  # страховые взносы
CALC_VERSION = 2  # меняется вместе с формулами: разборы в кэше и снапшоте пересчитываются при чтении
MARGIN = 0.25  # маржинальный доход с рубля выручки (оценка: какая часть изменения выручки доходит до прибыли)
PURCHASES_SHARE = 0.6  # закупки ≈ 60% выручки (оценка)
RATE_FACTORS = {"key_rate", "credit_conditions", "bond_market"}
# отраслевые факторы: через спрос (выручку) или через цены закупок; типовой шок, %, если в новости нет числа
BUSINESS_SHOCK = {"consumer_demand": 3, "construction": 5, "real_estate": 5, "auto_market": 5, "agriculture": 5,
                  "budget_policy": 5, "it_tech": 5, "commodity_oil": 10, "commodity_metals": 10, "gold": 10,
                  "fuel": 8, "logistics": 5, "sanctions_trade": 5, "geopolitics": 3, "inflation": 2}
VOLUME_FACTORS = {"consumer_demand", "construction", "auto_market", "agriculture"}
COST_FACTORS = {"fuel", "inflation", "logistics", "sanctions_trade"}  # для большинства компаний — статья затрат
TAX_FACTORS = {"tax_regulation"}
TAX_SHOCK_PP = 0.5  # п.п. выручки: стресс налоговой нагрузки
SHOCK_RE = re.compile(r"(?:на|[+−–-])\s?(\d{1,2}(?:[.,]\d)?)\s?%")
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
    margin: float | None = Field(None, ge=0, le=1)
    purchases_rub: float | None = Field(None, ge=0)
    demand_change_pct: float | None = Field(None, ge=-50, le=50)
    cost_change_pct: float | None = Field(None, ge=-50, le=100)
    tax_change_pp: float | None = Field(None, ge=-10, le=10)


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


MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def horizon_months(today: date | None = None) -> list[str]:
    """Подписи 12 месяцев горизонта, начиная со следующего месяца: «ноя 26», «дек 26», …"""
    t = today or date.today()
    out = []
    for k in range(1, 13):
        m = (t.month - 1 + k) % 12
        y = t.year + (t.month - 1 + k) // 12
        out.append(f"{MONTHS_RU[m]} {str(y)[2:]}")
    return out


def start_month(effective_dates: list[str] | None, today: date | None = None) -> int:
    """С какого месяца горизонта (0–11) начинается отраслевой эффект: норма вступает в силу позже — эффект позже."""
    t = today or date.today()
    for d in sorted(effective_dates or []):
        try:
            dd = date.fromisoformat(d[:10])
        except ValueError:
            continue
        if dd > t:
            k = (dd.year - t.year) * 12 + dd.month - t.month - 1
            return max(0, min(11, k))
    return 0


def _shock_from_text(text: str) -> float | None:
    """«продажи снизились на 5%», «+15%» → 5.0 / 15.0. Доли («74% платежей») без «на/±» не берём."""
    for m in SHOCK_RE.finditer(text or ""):
        v = float(m.group(1).replace(",", "."))
        if 1 <= v <= 25:
            return v
    return None


def business_levers(p: CompanyProfile, reasons: list[dict] | None) -> dict:
    """Отраслевые рычаги из причин релевантности: спрос (выручка), затраты (закупки), налоги.

    Канал выбирается по смыслу фактора и тому, чем событие связано с компанией:
    - спрос — фактор про рынок сбыта; доля выручки — покупатели из этой отрасли в графе платежей или своя отрасль
      (половина выручки, оценка). Связь только через «общий макрофон» рычаг не создаёт;
    - затраты — фактор про цены ресурсов (топливо, инфляция, логистика), а для сырья — когда рост цены против
      компании; доля закупок — поставщики из этой отрасли или все закупки, если компания прямо зависит от ресурса;
    - налоги — только если в новости речь о налогах, взносах, льготах или пошлинах.
    Сила шока масштабируется тем, насколько событие касается компании (вклад причины в релевантность).
    """
    mix = {(m["direction"], m["okved2"]): m for m in p.industry_mix}
    out: dict = {}
    for r in sorted(reasons or [], key=lambda r: -r.get("contribution", 0)):
        f = r.get("factor")
        if f not in BUSINESS_SHOCK and f not in TAX_FACTORS:
            continue
        effect = r.get("effect")
        sign = {"risk": -1, "opportunity": 1}.get(effect, 0)
        bases = list(r.get("basis") or []) + [x["basis"] for x in r.get("indirect") or []]
        # прямая связь с выручкой — только своя отрасль; продукты банка (кредит, лизинг) — не канал сбыта
        direct = any(b.startswith("profile:OKVED") for b in bases)
        direct_cost = direct or any(b.startswith(("profile:FTS", "profile:CL_PRODUCT_LEA")) for b in bases)
        bases = list(dict.fromkeys(bases))
        buyers = [mix[k] for b in bases if b.startswith("graph:in:") and (k := ("in", b.split(":")[2])) in mix]
        suppliers = [mix[k] for b in bases if b.startswith("graph:out:") and (k := ("out", b.split(":")[2])) in mix]
        intensity = max(0.25, min(1.0, r.get("contribution", 0.6) / 0.6))
        shock_news = _shock_from_text(r.get("evidence", ""))
        title = r.get("factor_title", f)
        if f in TAX_FACTORS:
            if "tax" not in out and re.search(r"налог|ндс|взнос|льгот|пошлин|акциз", (r.get("evidence") or "").lower()) \
                    and (direct or buyers):
                out["tax"] = {"factor": f, "title": title, "sign": sign, "share": 1.0,
                              "shock": round(TAX_SHOCK_PP * intensity, 2), "shock_src": "стресс-сценарий",
                              "basis": "налоговая нагрузка компании"}
            continue
        # число из новости берём как шок спроса только для объёмных показателей (продажи, оборот, ввод жилья);
        # рост цены аренды или сырья на 12% не означает рост спроса на 12%
        if f not in VOLUME_FACTORS and f not in COST_FACTORS:
            shock_news = None
        # через покупателей событие действует слабее, чем на собственный рынок (эффект второго порядка)
        indirect = 0.5 if buyers and not direct and f not in COST_FACTORS else 1.0
        shock = round((shock_news or BUSINESS_SHOCK[f]) * intensity * indirect, 1)
        src = "по данным новости" if shock_news else "типовой шок для фактора"
        if intensity * indirect < 1:
            src += ", с поправкой на силу связи"
        direction = r.get("direction")
        price_against = (direction == "up" and effect == "risk") or (direction == "down" and effect == "opportunity")
        is_cost = f in COST_FACTORS or (f in {"commodity_oil", "commodity_metals", "agriculture", "gold"} and price_against
                                        and not buyers)
        if is_cost:
            if "costs" in out or not (suppliers or direct_cost):
                continue
            share = min(1.0, sum(m["share"] for m in suppliers)) if suppliers else 1.0
            basis = ("поставщики: " + ", ".join(f"{m['name']} ({m['share']:.0%} платежей)" for m in suppliers)) if suppliers \
                else "закупки компании"
            out["costs"] = {"factor": f, "title": title, "sign": sign, "share": round(share, 3), "shock": shock,
                            "shock_src": src, "basis": basis}
        else:
            if "demand" in out or not (buyers or direct):
                continue
            if buyers:
                share = min(1.0, sum(m["share"] for m in buyers))
                basis = "покупатели: " + ", ".join(f"{m['name']} ({m['share']:.0%} поступлений)" for m in buyers)
            else:
                share, basis = 0.5, "своя отрасль — половина выручки (оценка)"
            out["demand"] = {"factor": f, "title": title, "sign": sign, "share": round(share, 3), "shock": shock,
                             "shock_src": src, "basis": basis}
    return out


def calc(p: CompanyProfile, inp: Inputs, spec: dict | None = None, start: int = 0) -> dict:
    """Эффект на прибыль за 12 месяцев. spec — отраслевые рычаги события (business_levers); start — месяц начала."""
    base = bank_baseline()
    d, est = defaults(p)
    v = {k: (getattr(inp, k) if getattr(inp, k) is not None else d.get(k)) for k in
         ("revenue_rub", "debt_rub", "floating_share", "deposits_rub", "import_rub", "export_rub", "pass_through",
          "payroll_rub", "planned_wage_growth_pct", "margin", "purchases_rub")}
    user_set = [k for k in v if getattr(inp, k) is not None]
    planned = v["planned_wage_growth_pct"] if v["planned_wage_growth_pct"] is not None else base["wage_growth_pct"] or 0
    wage = inp.wage_growth_pct if inp.wage_growth_pct is not None else planned
    rows = []
    if v["debt_rub"] or v["deposits_rub"]:
        debt = -(v["debt_rub"] or 0) * (v["floating_share"] or 0) * inp.rate_delta_pp / 100
        dep = (v["deposits_rub"] or 0) * inp.rate_delta_pp / 100
        rows.append({"lever": "rate", "title": "Ставка по кредитам и депозитам", "effect_rub": round(debt + dep),
                     "formula": f"−долг × доля под плавающей ставкой × ({num(inp.rate_delta_pp)} п.п.)"
                                + (" + доход по депозитам" if dep else ""), "basis": "кредиты и депозиты компании"})
    if v["import_rub"] or v["export_rub"]:
        imp = -(v["import_rub"] or 0) * inp.fx_change_pct / 100 * (1 - (v["pass_through"] or 0))
        exp = (v["export_rub"] or 0) * inp.fx_change_pct / 100
        rows.append({"lever": "fx", "title": "Курс рубля", "effect_rub": round(imp + exp),
                     "formula": f"−импорт × ({num(inp.fx_change_pct, 0)}%) × (1 − доля, переносимая в цены)"
                                + (" + выручка экспорта" if exp else ""), "basis": "внешнеторговые платежи"})
    if v["payroll_rub"] and (inp.wage_growth_pct is not None or inp.planned_wage_growth_pct is not None):
        rows.append({"lever": "wages", "title": "Зарплаты", "effect_rub": round(-v["payroll_rub"] * (wage - planned) / 100),
                     "formula": f"−ФОТ × (рост {num(wage)}% − {num(planned)}% заложено в бюджет по прогнозу банка)",
                     "basis": "фонд оплаты труда"})
    spec = spec or {}
    rev = v["revenue_rub"]
    active = (12 - start) / 12
    margin = v["margin"] if v["margin"] is not None else MARGIN
    purchases = v["purchases_rub"] if v["purchases_rub"] is not None else (rev * PURCHASES_SHARE if rev else None)
    if "demand" in spec and inp.demand_change_pct is not None and rev:
        sp = spec["demand"]
        eff = rev * sp["share"] * inp.demand_change_pct / 100 * margin * active
        rows.append({"lever": "demand", "title": f"Выручка: {_low(sp['title'])}", "effect_rub": round(eff),
                     "formula": f"выручка × {sp['share']:.0%} (затронутая доля) × ({num(inp.demand_change_pct, 1)}% спроса) "
                                f"× {margin:.0%} маржинальность" + (f" × {12 - start} мес. из 12" if start else ""),
                     "basis": sp["basis"]})
    if "costs" in spec and inp.cost_change_pct is not None and purchases:
        sp = spec["costs"]
        eff = -purchases * sp["share"] * inp.cost_change_pct / 100 * (1 - (v["pass_through"] or 0)) * active
        rows.append({"lever": "costs", "title": f"Закупки: {_low(sp['title'])}", "effect_rub": round(eff),
                     "formula": f"−закупки × {sp['share']:.0%} × ({num(inp.cost_change_pct, 1)}% цен) × (1 − перенос в цены)"
                                + (f" × {12 - start} мес. из 12" if start else ""), "basis": sp["basis"]})
    if "tax" in spec and inp.tax_change_pp is not None and rev:
        sp = spec["tax"]
        eff = -rev * inp.tax_change_pp / 100 * active
        rows.append({"lever": "tax", "title": "Налоговая нагрузка", "effect_rub": round(eff),
                     "formula": f"−выручка × ({num(inp.tax_change_pp, 1)} п.п.)" + (f" × {12 - start} мес. из 12" if start else ""),
                     "basis": sp["basis"]})
    if spec and rev and ("demand" in spec or "costs" in spec):
        est.append(f"маржинальность {margin:.0%}" + (f", закупки ≈ {PURCHASES_SHARE:.0%} выручки" if "costs" in spec else ""))
    total = sum(r["effect_rub"] for r in rows)
    return {"rows": rows, "total_rub": round(total), "share_of_revenue": round(total / rev, 4) if rev else None,
            "values": v, "estimated": est, "user_set": user_set, "baseline": base}


def relevant_levers(factors: list[str], spec: dict | None = None) -> list[str]:
    fs = set(factors)
    out = [lv for lv, group in (("rate", RATE_FACTORS), ("fx", FX_FACTORS), ("wages", WAGE_FACTORS)) if fs & group]
    out += [k for k in ("demand", "costs", "tax") if k in (spec or {})]
    return out or ["rate"]


# Форма накопления эффекта по месяцам: ставка и курс меняются постепенно, зарплаты индексируют с января,
# отраслевые эффекты — равномерно с месяца вступления в силу
def _shape(lever: str, months: list[str], start: int) -> list[float]:
    if lever in ("rate", "fx"):
        w = [k + 1 for k in range(12)]
    elif lever == "wages":
        jan = next((k for k, m in enumerate(months) if m.startswith("янв")), 0)
        w = [1 if k >= jan else 0 for k in range(12)]
    else:
        w = [1 if k >= start else 0 for k in range(12)]
    s = sum(w) or 1
    return [x / s for x in w]


def timeline(rows: list[dict], months: list[str], start: int) -> list[int]:
    """Накопленный эффект по месяцам (последнее значение = итог за 12 месяцев)."""
    monthly = [0.0] * 12
    for r in rows:
        for k, w in enumerate(_shape(r["lever"], months, start)):
            monthly[k] += r["effect_rub"] * w
    acc, out = 0.0, []
    for x in monthly:
        acc += x
        out.append(round(acc))
    return out


def scenarios(p: CompanyProfile, factors: list[str], user: Inputs | None = None, reasons: list[dict] | None = None,
              effective_dates: list[str] | None = None) -> list[dict]:
    """Три сценария на 12 месяцев: базовый (прогноз банка + данные новости), негативный и позитивный."""
    base = bank_baseline()
    spec = business_levers(p, reasons)
    levers = relevant_levers(factors, spec)
    start = start_month(effective_dates) if spec else 0
    months = horizon_months()
    u = (user or Inputs()).model_dump()

    def biz(kind: str, name: str) -> float | None:
        sp = spec.get(kind)
        if not sp:
            return None
        m, sg = sp["shock"], sp["sign"]
        # для затрат «риск» — рост цен (шок +), для спроса «риск» — падение (шок −)
        adverse = m if kind in ("costs", "tax") else -m
        if sg < 0:  # событие против компании: в базе шок наступает, в негативе — вдвое сильнее, в позитиве — нет
            k = {"base": 1, "negative": 2, "positive": 0}[name]
        elif sg > 0:  # в пользу компании
            k = {"base": -1, "negative": 0, "positive": -2}[name]
        else:  # неоднозначно: база — без изменений, сценарии — в обе стороны
            k = {"base": 0, "negative": 1, "positive": -1}[name]
        if kind == "tax" and sg != 0 and name == "base":
            k = k / 2  # налоговые изменения часто ещё обсуждаются: в базе — половина стресса
        return round(adverse * k, 2)

    plan = {
        "base": {"rate_delta_pp": base["rate_delta_pp"], "fx_change_pct": base["fx_change_pct"],
                 "wage_growth_pct": base["wage_growth_pct"]},
        "negative": {"rate_delta_pp": base["rate_delta_pp"] + 2, "fx_change_pct": base["fx_change_pct"] + 15,
                     "wage_growth_pct": (base["wage_growth_pct"] or 0) + 5},
        "positive": {"rate_delta_pp": base["rate_delta_pp"] - 1, "fx_change_pct": base["fx_change_pct"] - 5,
                     "wage_growth_pct": (base["wage_growth_pct"] or 0) - 3},
    }
    titles = {"base": "Базовый — прогноз Альфа-Банка" if not spec else "Базовый — прогноз банка и данные новости",
              "negative": "Негативный", "positive": "Позитивный"}
    out = []
    for name, shock in plan.items():
        params = {k: v for k, v in shock.items() if (k.startswith("rate") and "rate" in levers)
                  or (k.startswith("fx") and "fx" in levers) or (k.startswith("wage") and "wages" in levers)}
        for kind, key in (("demand", "demand_change_pct"), ("costs", "cost_change_pct"), ("tax", "tax_change_pp")):
            val = biz(kind, name)
            if val is not None:
                params[key] = val
        inp = Inputs(**{**{k: v for k, v in u.items() if v is not None and k not in params}, **params})
        res = calc(p, inp, spec, start)
        res["rows"] = [r for r in res["rows"] if r["lever"] in levers]
        res["total_rub"] = round(sum(r["effect_rub"] for r in res["rows"]))
        rev = res["values"]["revenue_rub"]
        res["share_of_revenue"] = round(res["total_rub"] / rev, 4) if rev else None
        srcs = [r for lv in levers if (r := base["refs"].get(lv))] if name == "base" else []
        out.append({"name": name, "title": titles[name], "params": params, "assumptions": _assumptions(params, base, spec),
                    "total_rub": res["total_rub"], "share_of_revenue": res["share_of_revenue"], "rows": res["rows"],
                    "estimated": res["estimated"], "monthly": timeline(res["rows"], months, start),
                    "months": months, "start_month": start, "version": CALC_VERSION,
                    "sources": (srcs or ["calc:scenario"]) if name == "base" else ["calc:scenario"]})
    return out


def _assumptions(params: dict, base: dict, spec: dict | None = None) -> str:
    parts = []
    spec = spec or {}
    if "rate_delta_pp" in params and base["key_rate_now"] is not None:
        parts.append(f"средняя ключевая ставка {num(base['key_rate_now'] + params['rate_delta_pp'])}% "
                     f"(сейчас {num(base['key_rate_now'])}%)")
    if "fx_change_pct" in params:
        parts.append(f"рубль {'слабее' if params['fx_change_pct'] > 0 else 'крепче'} на {abs(params['fx_change_pct']):.0f}%")
    if params.get("wage_growth_pct") is not None:
        parts.append(f"зарплаты +{num(params['wage_growth_pct'])}% за год")
    if params.get("demand_change_pct") is not None:
        v = params["demand_change_pct"]
        parts.append(f"спрос на продукцию {'без изменений' if not v else _pct(v)}")
    if params.get("cost_change_pct") is not None:
        v = params["cost_change_pct"]
        parts.append(f"цены закупок {'без изменений' if not v else _pct(v)}")
    if params.get("tax_change_pp") is not None:
        v = params["tax_change_pp"]
        parts.append("налоговая нагрузка без изменений" if not v else f"налоговая нагрузка {_pct(v, ' п.п. выручки')}")
    return "; ".join(parts)


def _low(t: str) -> str:
    return t[:1].lower() + t[1:] if not t[:2].isupper() else t


def _pct(v: float, unit: str = "%") -> str:
    return ("+" if v > 0 else "−") + num(abs(v)).replace(",0", "") + unit


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
