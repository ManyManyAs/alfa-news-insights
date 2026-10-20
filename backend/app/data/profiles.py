"""Сборка профилей компаний из CSV кейса (Приложение 1).

Файлы: data/case/*.csv.gz (в репозитории) или свои CSV в data/raw/ (приоритетнее):
  df_Uid_data_hack.csv      — компании
  df_Uid_payment_hack.csv   — платежи между компаниями
  df_Fid_Uid_link_hack.csv  — связи физлицо -> компания (пользователи интернет-банка)
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from app.data.graph import compute_graph_features
from app.data.models import CompanyProfile, Counterparty
from app.storage import db

log = logging.getLogger(__name__)

PRODUCT_COLUMNS = [
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FACTORING_MARK",
]
# Выручка ровно 1000 руб. встречается сотни раз — похоже на заглушку
SUSPICIOUS_REVENUE = {1000.0}
TOP_COUNTERPARTIES = 5


def find_table(directory: Path, stem: str) -> Path | None:
    """CSV кейса: <stem>.csv или сжатый <stem>.csv.gz (pandas читает оба)."""
    for name in (f"{stem}.csv", f"{stem}.csv.gz"):
        if (directory / name).exists():
            return directory / name
    return None


def _flag(v) -> bool:
    try:
        return float(v) == 1.0
    except (TypeError, ValueError):
        return False


def _clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def read_companies(path: Path, limit: int = 0) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", dtype=str)
    before = len(df)
    # В данных есть дубли SYNTHETIC_U: оставляем строку с наибольшим числом заполненных полей
    df["_filled"] = df.notna().sum(axis=1)
    df = df.sort_values("_filled", ascending=False).drop_duplicates("SYNTHETIC_U").drop(columns="_filled")
    if before != len(df):
        log.warning("Удалено дублей компаний: %d", before - len(df))
    df["REVENUE"] = pd.to_numeric(df["REVENUE_COMPANY"], errors="coerce")
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


def compute_counterparties(pay: pd.DataFrame, uids: set[str], okved: dict[str, tuple[str, str]]) -> dict[str, list[Counterparty]]:
    pay = pay[pay["PAYER"] != pay["BENEFICIARY"]]
    result: dict[str, list[Counterparty]] = {}

    for direction, own, other in (("in", "BENEFICIARY", "PAYER"), ("out", "PAYER", "BENEFICIARY")):
        sub = pay[pay[own].isin(uids)]
        agg = sub.groupby([own, other], as_index=False).agg(amount=("PAY_AMT", "sum"), count=("PAY_CNT", "sum"))
        totals = agg.groupby(own)["amount"].transform("sum")
        agg["share"] = agg["amount"] / totals
        agg = agg.sort_values([own, "amount"], ascending=[True, False])
        top = agg.groupby(own).head(TOP_COUNTERPARTIES)
        for row in top.itertuples(index=False):
            uid, cp = getattr(row, own), getattr(row, other)
            code, name = okved.get(cp, (None, None))
            result.setdefault(uid, []).append(
                Counterparty(uid=cp, direction=direction, amount=float(row.amount), count=int(row.count),
                             share=float(row.share), okved=code, okved_name=name)
            )
    return result


def build_profiles(raw_dir: Path, limit: int = 0) -> list[CompanyProfile]:
    df = read_companies(find_table(raw_dir, "df_Uid_data_hack"), limit)
    okved_map = {r.SYNTHETIC_U: (r.MAINOKVED_CODE, r.MAINOKVED_NAME) for r in df.itertuples(index=False)}

    valid_rev = df["REVENUE"].where(~df["REVENUE"].isin(SUSPICIOUS_REVENUE))
    df["_okved_total"] = valid_rev.groupby(df["MAINOKVED_CODE"]).transform("sum")
    df["_okved_peers"] = df.groupby("MAINOKVED_CODE")["SYNTHETIC_U"].transform("count")
    df["_okved_rank"] = valid_rev.groupby(df["MAINOKVED_CODE"]).rank(ascending=False, method="min")
    df["_group_total"] = valid_rev.groupby(df["GROUP_ID"]).transform("sum")
    df["_group_size"] = df.groupby("GROUP_ID")["SYNTHETIC_U"].transform("count")

    pay_path = find_table(raw_dir, "df_Uid_payment_hack")
    pay = pd.read_csv(pay_path, sep=";", dtype={"BENEFICIARY": str, "PAYER": str}) if pay_path else None
    cps = compute_counterparties(pay, set(df["SYNTHETIC_U"]), okved_map) if pay is not None else {}
    links_path = find_table(raw_dir, "df_Fid_Uid_link_hack")
    links = pd.read_csv(links_path, sep=";", dtype=str) if links_path else None
    graph = compute_graph_features(pay, df, links)

    profiles: list[CompanyProfile] = []
    for r in df.to_dict("records"):
        uid = r["SYNTHETIC_U"]
        rev = r["REVENUE"] if pd.notna(r["REVENUE"]) else None
        suspicious = rev in SUSPICIOUS_REVENUE
        gaps = []
        if suspicious or rev is None:
            gaps.append("выручка недостоверна или отсутствует")
        if _flag(r.get("FTS_FLAG")) and not _clean(r.get("FTS_COUNTRY_NAME")):
            gaps.append("ВЭД есть, но страны не указаны")
        if not _clean(r.get("ECO_MARK_LABEL")):
            gaps.append("не определена стадия жизненного цикла")
        countries = [c.strip() for c in (_clean(r.get("FTS_COUNTRY_NAME")) or "").split(",") if c.strip()]
        products = [c for c in PRODUCT_COLUMNS if _flag(r.get(c))]
        if _flag(r.get("FTS_FLAG")):
            products.append("FTS_FLAG")
        cp_list = cps.get(uid, [])
        buyers = [c.share for c in cp_list if c.direction == "in"]
        suppliers = [c.share for c in cp_list if c.direction == "out"]
        if not cp_list:
            gaps.append("нет данных о платежах с контрагентами")

        def ratio(num, den, suspicious=suspicious):
            return float(num / den) if (num is not None and den and pd.notna(den) and not suspicious) else None

        profiles.append(CompanyProfile(
            uid=uid,
            okved=r["MAINOKVED_CODE"],
            okved_name=r["MAINOKVED_NAME"],
            okfs=_clean(r.get("OKFS_NAME")),
            age=_clean(r.get("COMPANY_AGE_YEARS_GRADATION")),
            lifecycle_stage=_clean(r.get("ECO_MARK_LABEL")),
            key_owner_mark=_flag(r.get("NEGATIVEMARK")),
            workers=_clean(r.get("AMOUNT_WORKERS_FNS_GRADATION")) or _clean(r.get("AMOUNT_WORKERS_SPARK_GRADATION")),
            client_type=_clean(r.get("CL_TYPE")),
            products=products,
            fts=_flag(r.get("FTS_FLAG")),
            fts_countries=countries,
            revenue=rev,
            revenue_suspicious=suspicious,
            group_id=_clean(r.get("GROUP_ID")),
            group_size=int(r["_group_size"]),
            group_revenue_share=ratio(rev, r["_group_total"]),
            okved_peers=int(r["_okved_peers"]),
            okved_revenue_share=ratio(rev, r["_okved_total"]),
            okved_revenue_rank=int(r["_okved_rank"]) if pd.notna(r["_okved_rank"]) else None,
            city=_clean(r.get("CITY")),
            region=_clean(r.get("REGION")),
            counterparties=cp_list,
            top_buyer_share=max(buyers) if buyers else None,
            top_supplier_share=max(suppliers) if suppliers else None,
            data_gaps=gaps,
            **{k: v for k, v in graph.get(uid, {}).items() if k != "affiliates"},
            affiliates=graph.get(uid, {}).get("affiliates", [])[:20],
        ))
    return profiles


def data_quality_report(raw_dir: Path) -> dict:
    """Отчёт о качестве данных кейса (ограничение 05: неполные и зашумлённые данные)."""
    df = pd.read_csv(find_table(raw_dir, "df_Uid_data_hack"), sep=";", dtype=str)
    rev = pd.to_numeric(df["REVENUE_COMPANY"], errors="coerce")
    fts = df["FTS_FLAG"].astype(float) == 1.0
    return {
        "rows": int(len(df)), "unique_companies": int(df["SYNTHETIC_U"].nunique()),
        "duplicate_ids": int(len(df) - df["SYNTHETIC_U"].nunique()),
        "revenue_stub_1000": int((rev == 1000).sum()), "revenue_missing": int(rev.isna().sum()),
        "lifecycle_missing": int(df["ECO_MARK_LABEL"].isna().sum()),
        "workers_spark_missing_share": round(float(df["AMOUNT_WORKERS_SPARK_GRADATION"].isna().mean()), 3),
        "fts_without_country": int((fts & df["FTS_COUNTRY_NAME"].isna()).sum()),
        "client_types": df["CL_TYPE"].value_counts().to_dict(),
        "handling": {
            "duplicate_ids": "оставляем строку с максимумом заполненных полей",
            "revenue_stub_1000": "не участвует в долях рынка и группы, в профиле — «выручка недостоверна»",
            "fts_without_country": "ВЭД учитывается, страна — пробел в данных (показывается пользователю)",
            "counterparties_outside_sample": "доля платежей с контрагентами вне выборки показывается как «отрасль неизвестна»",
        },
    }


def save_profiles(profiles: list[CompanyProfile]) -> None:
    with db.tx() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO companies(uid, okved, group_id, profile) VALUES (?,?,?,?)",
            [(p.uid, p.okved, p.group_id, p.model_dump_json()) for p in profiles],
        )


def load_user_links(path: Path) -> int:
    df = pd.read_csv(path, sep=";", dtype=str)
    rows = [(r.SYNTHETIC_F, r.SYNTHETIC_U, r.CITY_NAME, r.RC_NAME) for r in df.itertuples(index=False)]
    with db.tx() as conn:
        conn.executemany("INSERT OR REPLACE INTO user_links(fid, uid, city, rc_name) VALUES (?,?,?,?)", rows)
    return len(rows)


def get_profile(uid: str) -> CompanyProfile | None:
    row = db.query_one("SELECT profile FROM companies WHERE uid=?", (uid,))
    return CompanyProfile.model_validate_json(row["profile"]) if row else None


def companies_of_user(fid: str) -> list[str]:
    return [r["uid"] for r in db.query("SELECT uid FROM user_links WHERE fid=?", (fid,))]
