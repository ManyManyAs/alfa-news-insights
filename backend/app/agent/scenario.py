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
CALC_VERSION = 5  # меняется вместе с формулами: разборы в кэше и снапшоте пересчитываются при чтении
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
FX_FACTORS_STRICT = {"fx_rub"}  # курс в сценарии — только если новость про сам курс рубля (не про торговлю с Китаем вообще)
# Ставка ЦБ и зарплаты в России: новости только о других странах (ЕЦБ, зарплаты в Skoda) эти рычаги не двигают
DOMESTIC_LEVERS = {"rate", "wages"}
# Отклонения от прогноза банка в негативном и позитивном сценариях: умеренные, без «катастроф»
RATE_DEV = (1.0, -1.0)    # п.п. ключевой ставки: снижение не началось / пошло быстрее прогноза
FX_DEV = (10.0, -5.0)     # % курса: рубль слабее / крепче прогноза
WAGE_DEV = (3.0, -2.0)    # п.п. роста зарплат сверх / ниже прогноза
CAP_SHARE = 0.015         # отраслевой рычаг — не больше 1,5% выручки в любом сценарии
NEGLIGIBLE_RUB, NEGLIGIBLE_SHARE = 150_000, 0.0005  # меньше — «заметного денежного эффекта нет»
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


def business_levers(p: CompanyProfile, reasons: list[dict] | None, regions: list[str] | None = None) -> dict:
    """Отраслевые рычаги события — только там, где деньги можно оценить честно.

    Рычаг появляется, если одновременно:
    - в новости есть число изменения объёмного показателя («продажи −5%», «оборот +5,3%») или цены ресурса
      («бензин +26%») — без числа сумму не придумываем;
    - эффект для компании однозначен (риск или возможность, а не «следить»);
    - компания прямо связана с этим рынком: своя отрасль или покупатели/поставщики из отрасли с долей ≥ 20%;
    - событие касается региона компании или её контрагентов либо всей страны.
    Сила переноса консервативна: своя отрасль — половина изменения рынка, через контрагентов — четверть.
    """
    mix = {(m["direction"], m["okved2"]): m for m in p.industry_mix}
    my_regions = {p.region} | {r["region"] for r in p.cp_regions if r.get("share", 0) >= 0.2}
    if regions and not (set(regions) & my_regions):
        return {}  # региональная новость о чужом регионе: денежного эффекта для компании не считаем
    out: dict = {}
    for r in sorted(reasons or [], key=lambda r: -r.get("contribution", 0)):
        f, effect = r.get("factor"), r.get("effect")
        if f not in VOLUME_FACTORS and f not in COST_FACTORS:
            continue
        sign = {"risk": -1, "opportunity": 1}.get(effect)
        shock = _shock_from_text(r.get("evidence", ""))
        if sign is None or shock is None:
            continue
        bases = list(dict.fromkeys(list(r.get("basis") or []) + [x["basis"] for x in r.get("indirect") or []]))
        direct = any(b.startswith("profile:OKVED") for b in bases)
        buyers = [mix[k] for b in bases if b.startswith("graph:in:") and (k := ("in", b.split(":")[2])) in mix
                  and mix[k]["share"] >= 0.2]
        suppliers = [mix[k] for b in bases if b.startswith("graph:out:") and (k := ("out", b.split(":")[2])) in mix
                     and mix[k]["share"] >= 0.2]
        title = r.get("factor_title", f)
        if f in COST_FACTORS:
            if "costs" in out or not (suppliers or direct):
                continue
            share = min(1.0, sum(m["share"] for m in suppliers)) if suppliers else 0.5
            basis = ("поставщики: " + ", ".join(f"{m['name']} ({m['share']:.0%} платежей)" for m in suppliers)) if suppliers \
                else "ресурс — заметная статья затрат отрасли (оценка: половина закупок)"
            out["costs"] = {"factor": f, "title": title, "sign": sign, "share": round(share, 3), "shock": shock,
                            "pass": 0.5 if suppliers else 1.0, "basis": basis}
        else:
            if "demand" in out or not (buyers or direct):
                continue
            if direct:
                share, pass_, basis = 1.0, 0.5, "своя отрасль: половина изменения рынка доходит до выручки (оценка)"
            else:
                share = min(1.0, sum(m["share"] for m in buyers))
                pass_ = 0.25
                basis = "покупатели: " + ", ".join(f"{m['name']} ({m['share']:.0%} поступлений)" for m in buyers) \
                    + "; до компании доходит четверть изменения их рынка (оценка)"
            out["demand"] = {"factor": f, "title": title, "sign": sign, "share": round(share, 3), "shock": shock,
                             "pass": pass_, "basis": basis}
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
                     "formula": f"−долг × доля под плавающей ставкой × ({num(inp.rate_delta_pp)} п.п. к прогнозу банка)"
                                + (" + доход по депозитам" if dep else ""), "basis": "кредиты и депозиты компании"})
    if v["import_rub"] or v["export_rub"]:
        imp = -(v["import_rub"] or 0) * inp.fx_change_pct / 100 * (1 - (v["pass_through"] or 0))
        exp = (v["export_rub"] or 0) * inp.fx_change_pct / 100
        rows.append({"lever": "fx", "title": "Курс рубля", "effect_rub": round(imp + exp),
                     "formula": f"−импорт × ({num(inp.fx_change_pct, 0)}% к прогнозу курса) × (1 − доля, переносимая в цены)"
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
                     "formula": f"выручка × {sp['share']:.0%} × ({num(inp.demand_change_pct, 1)}% спроса для компании) "
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
    """Рычаги, которые двигает само событие. Без «рычага по умолчанию»: если событие не про ставку, курс,
    зарплаты или измеримый рынок компании — денежной оценки нет."""
    fs = set(factors)
    out = [lv for lv, group in (("rate", RATE_FACTORS), ("fx", FX_FACTORS_STRICT), ("wages", WAGE_FACTORS)) if fs & group]
    return out + [k for k in ("demand", "costs") if k in (spec or {})]


def primary_factors(reasons: list[dict] | None, factors: list[str]) -> list[str]:
    """Главные факторы события для компании: причины со связью через профиль (не только общий макрофон)
    и с понятным направлением. Без причин (старые вызовы) — переданный список."""
    if reasons is None:
        return factors
    top = [r for r in sorted(reasons, key=lambda r: -r.get("contribution", 0))
           if r.get("factor") not in ("country", "industry", "region", "watch")
           and r.get("direction") in ("up", "down", "stable") and any(b != "baseline" for b in r.get("basis") or [])]
    return [r["factor"] for r in top[:2]]


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
              effective_dates: list[str] | None = None, regions: list[str] | None = None,
              countries: list[str] | None = None) -> list[dict]:
    """Три сценария на 12 месяцев относительно плана компании.

    База — прогноз Альфа-Банка и текущая динамика рынков: считаем, что они уже заложены в план, поэтому в базе 0.
    Негативный и позитивный — умеренные отклонения: ставка ±1 п.п., курс +10/−5%, зарплаты +3/−2 п.п. к прогнозу
    банка. Отраслевой рычаг (выручка, закупки) есть только при числе в новости и прямой связи с компанией
    (business_levers): тенденция продолжится — полная сила переноса, развернётся — половина в обратную сторону.
    Если эффект меньше 150 тыс. ₽ и 0,05% выручки — помечаем как несущественный, суммы не показываем.
    """
    base = bank_baseline()
    spec = business_levers(p, reasons, regions)
    levers = relevant_levers(primary_factors(reasons, factors), spec)
    if countries and not any(c.upper() in ("РОССИЯ", "РФ") for c in countries) and not regions:
        levers = [lv for lv in levers if lv not in DOMESTIC_LEVERS]  # событие о другой стране
    start = start_month(effective_dates) if spec else 0
    months = horizon_months()
    u = (user or Inputs()).model_dump()
    fx_rel = bool(p.fts)
    plan_w = base["wage_growth_pct"] or 0

    def macro(name: str) -> dict:
        k = {"base": None, "negative": 0, "positive": 1}[name]
        out: dict = {}
        if "rate" in levers:
            out["rate_delta_pp"] = 0.0 if k is None else RATE_DEV[k]
        if "fx" in levers and fx_rel:
            out["fx_change_pct"] = 0.0 if k is None else FX_DEV[k]
        if "wages" in levers:
            out["wage_growth_pct"] = plan_w + (0.0 if k is None else WAGE_DEV[k])
        return out

    def biz(kind: str, name: str) -> float | None:
        sp = spec.get(kind)
        if not sp:
            return None
        # База — 0: цифра из новости чаще описывает уже случившееся, а текущая динамика обычно уже в плане.
        # Сценарии — что будет, если тенденция из новости продолжится (полная сила переноса) или развернётся (половина).
        m = sp["shock"] * sp["pass"]
        favourable = ({"base": 0.0, "negative": -0.5, "positive": 1.0} if sp["sign"] > 0
                      else {"base": 0.0, "negative": -1.0, "positive": 0.5})
        k = favourable[name]  # знак: + — в пользу компании
        return round(m * k if kind == "demand" else -m * k, 2)

    titles = {"base": "Базовый — прогноз Альфа-Банка", "negative": "Негативный", "positive": "Позитивный"}
    out = []
    for name in ("base", "negative", "positive"):
        params = macro(name)
        for kind, key in (("demand", "demand_change_pct"), ("costs", "cost_change_pct")):
            val = biz(kind, name)
            if val is not None:
                params[key] = val
        inp = Inputs(**{**{k: v for k, v in u.items() if v is not None and k not in params}, **params})
        res = calc(p, inp, spec, start)
        rev = res["values"]["revenue_rub"]
        rows = [r for r in res["rows"] if r["lever"] in levers and (r["lever"] != "fx" or fx_rel)]
        for r in rows:  # потолок для отраслевых рычагов: не больше 1,5% выручки
            if r["lever"] in ("demand", "costs") and rev and abs(r["effect_rub"]) > CAP_SHARE * rev:
                r["effect_rub"] = round(CAP_SHARE * rev * (1 if r["effect_rub"] > 0 else -1))
                r["formula"] += f" · ограничено {CAP_SHARE:.1%} выручки"
        total = round(sum(r["effect_rub"] for r in rows))
        srcs = [r for lv in levers if (r := base["refs"].get(lv))] if name == "base" else []
        out.append({"name": name, "title": titles[name], "params": params, "assumptions": _assumptions(params, base, spec),
                    "total_rub": total, "share_of_revenue": round(total / rev, 4) if rev else None, "rows": rows,
                    "estimated": res["estimated"], "monthly": timeline(rows, months, start),
                    "months": months, "start_month": start, "version": CALC_VERSION,
                    "sources": (srcs or ["calc:scenario"]) if name == "base" else ["calc:scenario"]})
    rev = defaults(p)[0].get("revenue_rub")
    peak = max((abs(s["total_rub"]) for s in out), default=0)
    negligible = not any(s["rows"] for s in out) or peak < max(NEGLIGIBLE_RUB, NEGLIGIBLE_SHARE * (rev or 0))
    sens = sensitivity(p, u, plan_w, fx_rel, levers)
    for s in out:
        s["negligible"] = negligible
        s["sensitivity"] = sens
        s["event_levers"] = levers
    return out


def sensitivity(p: CompanyProfile, u: dict, plan_w: float, fx_rel: bool, levers: list[str]) -> list[dict]:
    """Чувствительность компании — не прогноз по событию: сколько за 12 месяцев стоит отклонение от прогноза банка
    на типовую величину (ставка +1 п.п., рубль слабее на 10%, зарплаты быстрее на 1 п.п.). Показывается и тогда, когда
    событие денег не меняет: клиент видит, к чему компания чувствительна, и что можно ограничить инструментами банка."""
    shocks = (("rate", "Ставка на 1 п.п. выше прогноза банка", {"rate_delta_pp": 1.0}),
              ("fx", "Рубль на 10% слабее прогноза банка", {"fx_change_pct": 10.0}),
              ("wages", "Зарплаты растут на 1 п.п. быстрее прогноза банка", {"wage_growth_pct": plan_w + 1.0}))
    out = []
    for lever, title, kw in shocks:
        if lever == "fx" and not fx_rel:
            continue
        inp = Inputs(**{**{k: v for k, v in u.items() if v is not None and k not in kw}, **kw})
        row = next((r for r in calc(p, inp, None, 0)["rows"] if r["lever"] == lever), None)
        if row and abs(row["effect_rub"]) >= 1000:
            out.append({"lever": lever, "title": title, "effect_rub": row["effect_rub"], "formula": row["formula"],
                        "event_lever": lever in levers})
    return out


def _assumptions(params: dict, base: dict, spec: dict | None = None) -> str:
    parts = []
    spec = spec or {}
    if "rate_delta_pp" in params and base["key_rate_avg_next"] is not None:
        d = params["rate_delta_pp"]
        parts.append(f"средняя ставка {num(base['key_rate_avg_next'] + d)}%" + (" — как в прогнозе банка" if not d
                     else f" ({_pct(d, ' п.п.')} к прогнозу банка)"))
    if "fx_change_pct" in params:
        d = params["fx_change_pct"]
        parts.append("курс — как в прогнозе банка" if not d else f"рубль {'слабее' if d > 0 else 'крепче'} прогноза на {abs(d):.0f}%")
    if params.get("wage_growth_pct") is not None:
        parts.append(f"зарплаты +{num(params['wage_growth_pct'])}% за год")
    if params.get("demand_change_pct") is not None:
        v = params["demand_change_pct"]
        parts.append("спрос — как в плане" if not v else f"спрос для компании {_pct(v)} к плану (тенденция из новости)")
    if params.get("cost_change_pct") is not None:
        v = params["cost_change_pct"]
        parts.append("цены закупок — как в плане" if not v else f"цены закупок {_pct(v)} к плану (тенденция из новости)")
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
