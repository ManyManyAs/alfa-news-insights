"""Граф компаний: платежи, связанные физлица, группы — признаки для персонализации (этап 3).

* отраслевой профиль контрагентов: доли входящих/исходящих платежей по классам ОКВЭД (агрегат по ВСЕМ
  контрагентам, а не только топ-5) → эффекты второго порядка «событие бьёт по отрасли ваших покупателей»;
* концентрация (индекс Херфиндаля–Хиршмана) по покупателям и поставщикам;
* PageRank в сети денежных потоков → «насколько компания — узел сети» (перцентиль);
* аффилированность через общих связанных физлиц (df_Fid_Uid_link_hack);
* география контрагентов.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
MAX_PERSON_COMPANIES = 25  # физлицо, связанное с большим числом компаний, — «шум» (бухгалтер, массовый директор)

OKVED_CLASSES = {
    "01": "Сельское хозяйство", "02": "Лесное хозяйство", "03": "Рыболовство", "05": "Добыча угля", "06": "Добыча нефти и газа",
    "07": "Добыча металлических руд", "08": "Добыча прочих полезных ископаемых", "09": "Услуги в добыче", "10": "Пищевая промышленность",
    "11": "Производство напитков", "13": "Текстильное производство", "14": "Производство одежды", "15": "Кожа и обувь",
    "16": "Деревообработка", "17": "Бумажное производство", "18": "Полиграфия", "19": "Нефтепереработка", "20": "Химическое производство",
    "21": "Фармацевтика", "22": "Резина и пластмассы", "23": "Стройматериалы (неметаллические)", "24": "Металлургия",
    "25": "Металлоизделия", "26": "Электроника", "27": "Электрооборудование", "28": "Машиностроение", "29": "Автомобилестроение",
    "30": "Прочие транспортные средства", "31": "Мебель", "32": "Прочие производства", "33": "Ремонт и монтаж оборудования",
    "35": "Энергетика", "36": "Водоснабжение", "37": "Водоотведение", "38": "Обработка отходов", "41": "Строительство зданий",
    "42": "Инженерные сооружения", "43": "Специализированные строительные работы", "45": "Торговля автомобилями и сервис",
    "46": "Оптовая торговля", "47": "Розничная торговля", "49": "Сухопутный транспорт", "50": "Водный транспорт",
    "51": "Авиаперевозки", "52": "Складирование и логистика", "53": "Почта и курьеры", "55": "Гостиницы", "56": "Общепит",
    "58": "Издательская деятельность", "59": "Кино и видео", "60": "Теле- и радиовещание", "61": "Телеком",
    "62": "Разработка ПО", "63": "Информационные услуги", "64": "Финансовые услуги", "65": "Страхование",
    "66": "Вспомогательные финансовые услуги", "68": "Недвижимость", "69": "Право и бухучёт", "70": "Консалтинг",
    "71": "Инжиниринг и проектирование", "72": "НИОКР", "73": "Реклама и маркетинг", "74": "Прочие профессиональные услуги",
    "75": "Ветеринария", "77": "Аренда и лизинг", "78": "Кадровые услуги", "79": "Туризм", "80": "Охрана",
    "81": "Обслуживание зданий", "82": "Административные услуги", "84": "Госуправление", "85": "Образование",
    "86": "Здравоохранение", "87": "Уход с проживанием", "88": "Социальные услуги", "90": "Культура и искусство",
    "91": "Библиотеки и музеи", "92": "Азартные игры", "93": "Спорт и отдых", "94": "Общественные организации",
    "95": "Ремонт компьютеров и бытовых товаров", "96": "Персональные услуги",
}


def okved_class_name(code: str | None) -> str:
    if not code:
        return "Неизвестная отрасль"
    return OKVED_CLASSES.get(code[:2], f"ОКВЭД {code[:2]}")


def pagerank(pay: pd.DataFrame, iterations: int = 30, d: float = 0.85) -> dict[str, float]:
    """PageRank по денежным потокам (ребро плательщик → получатель, вес — сумма). Без scipy: numpy.bincount."""
    nodes = pd.Index(pd.unique(pd.concat([pay["PAYER"], pay["BENEFICIARY"]], ignore_index=True)))
    n = len(nodes)
    if n == 0:
        return {}
    src = nodes.get_indexer(pay["PAYER"])
    dst = nodes.get_indexer(pay["BENEFICIARY"])
    w = pay["PAY_AMT"].to_numpy(dtype=float)
    out_w = np.bincount(src, weights=w, minlength=n)
    norm = w / out_w[src]
    dangling = out_w == 0
    pr = np.full(n, 1.0 / n)
    for _ in range(iterations):
        contrib = np.bincount(dst, weights=pr[src] * norm, minlength=n)
        pr = (1 - d) / n + d * (contrib + pr[dangling].sum() / n)
    return dict(zip(nodes, pr, strict=True))


def compute_graph_features(pay: pd.DataFrame | None, companies: pd.DataFrame, links: pd.DataFrame | None) -> dict[str, dict]:
    """uid → {industry_mix, hhi_in, hhi_out, n_buyers, n_suppliers, network_pct, cp_regions, affiliates}."""
    feats: dict[str, dict] = defaultdict(dict)
    uids = set(companies["SYNTHETIC_U"])
    okved = dict(zip(companies["SYNTHETIC_U"], companies["MAINOKVED_CODE"], strict=True))
    region = dict(zip(companies["SYNTHETIC_U"], companies["REGION"], strict=True))

    if pay is not None and len(pay):
        pay = pay[pay["PAYER"] != pay["BENEFICIARY"]]
        cls_of = {u: (c or "")[:2] for u, c in okved.items()}
        for direction, own, other in (("in", "BENEFICIARY", "PAYER"), ("out", "PAYER", "BENEFICIARY")):
            sub = pay[pay[own].isin(uids)]
            per_cp = sub.groupby([own, other], as_index=False, sort=False)["PAY_AMT"].sum()
            per_cp["share"] = per_cp["PAY_AMT"] / per_cp.groupby(own)["PAY_AMT"].transform("sum")
            per_cp["sq"] = per_cp["share"] ** 2
            agg = per_cp.groupby(own).agg(hhi=("sq", "sum"), n=(other, "count"))
            per_cp["cls"] = per_cp[other].map(cls_of).fillna("??").replace("", "??")
            per_cp["reg"] = per_cp[other].map(region)
            mix = per_cp.groupby([own, "cls"], as_index=False, sort=False)["share"].sum()
            unknown = mix[mix["cls"] == "??"].set_index(own)["share"]
            known = mix[(mix["cls"] != "??") & (mix["share"] >= 0.03)].sort_values([own, "share"], ascending=[True, False])
            known = known.groupby(own).head(6)
            regs = per_cp.dropna(subset=["reg"]).groupby([own, "reg"], as_index=False, sort=False)["share"].sum()
            regs = regs.sort_values([own, "share"], ascending=[True, False]).groupby(own).head(3)
            n_key = "n_buyers" if direction == "in" else "n_suppliers"
            for uid, hhi, n in zip(agg.index, agg["hhi"].to_numpy(), agg["n"].to_numpy(), strict=True):
                f = feats[uid]
                f[f"hhi_{direction}"] = round(float(hhi), 4)
                f[n_key] = int(n)
                f[f"unknown_share_{direction}"] = 0.0
            for uid, sh in unknown.items():
                feats[uid][f"unknown_share_{direction}"] = round(float(sh), 4)
            for uid, c, sh in zip(known[own].to_numpy(), known["cls"].to_numpy(), known["share"].to_numpy(), strict=True):
                feats[uid].setdefault("industry_mix", []).append(
                    {"direction": direction, "okved2": c, "name": okved_class_name(c), "share": round(float(sh), 4)})
            for uid, rg, sh in zip(regs[own].to_numpy(), regs["reg"].to_numpy(), regs["share"].to_numpy(), strict=True):
                feats[uid].setdefault("cp_regions", []).append({"direction": direction, "region": rg, "share": round(float(sh), 3)})
        pr = pagerank(pay)
        vals = pd.Series({u: pr.get(u, 0.0) for u in uids})
        pct = vals.rank(pct=True)
        for uid, p in pct.items():
            if pr.get(uid):
                feats[uid]["network_pct"] = round(float(p), 4)

    if links is not None and len(links):
        lk = links[links["SYNTHETIC_U"].isin(uids)].drop_duplicates(["SYNTHETIC_F", "SYNTHETIC_U"])
        size = lk.groupby("SYNTHETIC_F")["SYNTHETIC_U"].transform("count")
        lk = lk[size.between(2, MAX_PERSON_COMPANIES)]
        aff: dict[str, set[str]] = defaultdict(set)
        for comps in lk.groupby("SYNTHETIC_F")["SYNTHETIC_U"].agg(list):
            for u in comps:
                aff[u].update(c for c in comps if c != u)
        for uid, others in aff.items():
            feats[uid]["affiliates"] = sorted(others)
    return feats


REVENUE_BANDS = [(0, 1e7, "до 10 млн"), (1e7, 1e8, "10–100 млн"), (1e8, 1e9, "100 млн – 1 млрд"),
                 (1e9, 1e10, "1–10 млрд"), (1e10, float("inf"), "более 10 млрд")]


def revenue_band(rev: float | None, suspicious: bool) -> str:
    if rev is None or suspicious:
        return "выручка неизвестна"
    return next(label for lo, hi, label in REVENUE_BANDS if lo <= rev < hi)
