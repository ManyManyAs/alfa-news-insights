"""Похожие компании и бенчмарки (этап 3).

Группа сравнения = тот же класс ОКВЭД (4 знака) + та же полоса выручки; если компаний меньше 15 —
расширяем до 2 знаков ОКВЭД, затем до одной отрасли без учёта выручки. Для группы считаются доли
продуктов банка, ВЭД, медианы концентрации контрагентов и т.п. Используются:
* как бенчмарк в интерфейсе и в инсайтах («зарплатный проект есть у 61% похожих компаний»);
* как «холодный старт» персонализации для компаний с бедным профилем (только РКО, нет платежей).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from app.data.graph import okved_class_name, revenue_band
from app.data.models import CompanyProfile
from app.storage import db

MIN_PEERS = 15
PRODUCTS = ["CL_PRODUCT_RKO_MARK", "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_ZPP_MARK",
            "CL_PRODUCT_GAR_MARK", "FACTORING_MARK", "FTS_FLAG"]
PRODUCT_TITLES = {"CL_PRODUCT_RKO_MARK": "РКО", "CL_PRODUCT_LOA_MARK": "кредит", "CL_PRODUCT_DEP_MARK": "депозит",
                  "CL_PRODUCT_LEA_MARK": "лизинг", "CL_PRODUCT_ZPP_MARK": "зарплатный проект", "CL_PRODUCT_GAR_MARK": "банковские гарантии",
                  "FACTORING_MARK": "факторинг", "FTS_FLAG": "ВЭД"}


def assign_peer_groups(profiles: list[CompanyProfile]) -> dict[str, dict]:
    """Проставляет profile.peer_key/peers_n и возвращает статистику групп {key: stats}."""
    df = pd.DataFrame({
        "uid": [p.uid for p in profiles],
        "o4": [p.okved[:5] for p in profiles],
        "o2": [p.okved[:2] for p in profiles],
        "band": [revenue_band(p.revenue, p.revenue_suspicious) for p in profiles],
        "hhi_in": [p.hhi_in for p in profiles],
        "rev": [None if p.revenue_suspicious else p.revenue for p in profiles],
        **{k: [k in p.products for p in profiles] for k in PRODUCTS},
    })
    n4 = df.groupby(["o4", "band"])["uid"].transform("count")
    n2 = df.groupby(["o2", "band"])["uid"].transform("count")
    df["peer_key"] = np.where(n4 >= MIN_PEERS, "o4:" + df["o4"] + "|" + df["band"],
                              np.where(n2 >= MIN_PEERS, "o2:" + df["o2"] + "|" + df["band"], "o2:" + df["o2"] + "|*"))
    keys = df["peer_key"].tolist()
    stats: dict[str, dict] = {}
    for key, g in df.groupby("peer_key"):
        level, rest = key.split(":", 1)
        code, band = rest.split("|")
        stats[key] = {
            "key": key, "n": int(len(g)), "okved": code, "okved_name": okved_class_name(code) if level == "o2" else None,
            "revenue_band": None if band == "*" else band,
            "products": {k: round(float(g[k].mean()), 3) for k in PRODUCTS},
            "median_hhi_in": None if g["hhi_in"].isna().all() else round(float(g["hhi_in"].median()), 3),
            "median_revenue": None if g["rev"].isna().all() else float(g["rev"].median()),
        }
    for p, key in zip(profiles, keys, strict=True):
        p.peer_key = key
        p.peers_n = stats[key]["n"]
    return stats


def save_peer_groups(stats: dict[str, dict]) -> None:
    with db.tx() as conn:
        conn.executemany("INSERT OR REPLACE INTO peer_groups(key, payload) VALUES (?,?)",
                         [(k, json.dumps(v, ensure_ascii=False)) for k, v in stats.items()])


def peer_stats(key: str | None) -> dict | None:
    if not key:
        return None
    row = db.query_one("SELECT payload FROM peer_groups WHERE key=?", (key,))
    return json.loads(row["payload"]) if row else None


def peer_benchmark(p: CompanyProfile) -> dict | None:
    """Сравнение компании с группой: какие продукты/признаки есть у большинства похожих, а у неё нет — и наоборот."""
    st = peer_stats(p.peer_key)
    if not st:
        return None
    rows = []
    for k, share in st["products"].items():
        has = k in p.products
        rows.append({"product": k, "title": PRODUCT_TITLES[k], "peers_share": share, "has": has,
                     "note": ("есть у компании" if has else "нет у компании") + f", у похожих — {share:.0%}"})
    rows.sort(key=lambda r: -r["peers_share"])
    level, rest = st["key"].split(":", 1)
    desc = (f"{st['n']} компаний с ОКВЭД {st['okved']}" if level == "o4" else f"{st['n']} компаний отрасли «{st['okved_name']}»")
    if st["revenue_band"]:
        desc += f" и выручкой {st['revenue_band']}"
    conc = None
    if p.hhi_in is not None and st["median_hhi_in"] is not None:
        conc = "выше" if p.hhi_in > st["median_hhi_in"] * 1.2 else "ниже" if p.hhi_in < st["median_hhi_in"] * 0.8 else "на уровне"
    return {"peer_key": st["key"], "description": desc, "n": st["n"], "products": rows,
            "buyer_concentration_vs_peers": conc, "median_revenue": st["median_revenue"]}
