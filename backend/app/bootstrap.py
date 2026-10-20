"""Первичная загрузка: профили компаний, связи пользователей, демо-новости, демо-пользователи."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import get_settings
from app.data.profiles import build_profiles, load_user_links, save_profiles
from app.news.ingest import run_ingest
from app.storage import db

log = logging.getLogger(__name__)

# Демо-сценарии: подбираем реальных пользователей из данных под разные типы бизнеса
DEMO_CRITERIA = [
    ("Девелопер с кредитом", "cfo", lambda p: p["okved"].startswith("41") and "CL_PRODUCT_LOA_MARK" in p["products"]),
    ("Импортёр (Китай), оптовая торговля", "treasurer", lambda p: p["okved"].startswith("46") and "КИТАЙ" in [c.upper() for c in p["fts_countries"]]),
    ("IT-компания с зарплатным проектом", "accountant", lambda p: p["okved"].startswith("62") and "CL_PRODUCT_ZPP_MARK" in p["products"]),
    ("Ресторанный бизнес", "owner", lambda p: p["okved"].startswith("56")),
    ("Арендодатель недвижимости с депозитом", "cfo", lambda p: p["okved"].startswith("68") and "CL_PRODUCT_DEP_MARK" in p["products"]),
    ("Перевозчик с лизингом", "ceo", lambda p: p["okved"].startswith("49") and "CL_PRODUCT_LEA_MARK" in p["products"]),
]


def data_source_dir() -> Path:
    s = get_settings()
    if s.data_source_dir:
        return s.data_source_dir
    if (s.raw_dir / "df_Uid_data_hack.csv").exists():
        return s.raw_dir
    log.warning("data/raw пуст — используем синтетические данные из data/sample")
    return s.data_dir / "sample"


def demo_users_path() -> Path:
    db_path = get_settings().db_path
    return db_path.parent / f"{db_path.stem}.demo_users.json"


def pick_demo_users() -> list[dict]:
    rows = db.query("""SELECT ul.fid, c.uid, c.profile FROM user_links ul JOIN companies c ON c.uid = ul.uid""")
    by_fid: dict[str, list[dict]] = {}
    for r in rows:
        by_fid.setdefault(r["fid"], []).append(json.loads(r["profile"]))
    users, used = [], set()
    for label, role, pred in DEMO_CRITERIA:
        best = None
        for fid, profs in by_fid.items():
            if fid in used:
                continue
            match = [p for p in profs if pred(p)]
            if match:
                p = max(match, key=lambda x: (len(x["counterparties"]) > 0, x.get("revenue") or 0))
                cand = (len(p["counterparties"]) > 0, len(p["products"]), fid, p["uid"])
                if best is None or cand > best:
                    best = cand
        if best:
            used.add(best[2])
            users.append({"user_id": best[2], "label": label, "role": role, "primary_company": best[3],
                          "companies": [p["uid"] for p in by_fid[best[2]]]})
    demo_users_path().write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    return users


def load_demo_users() -> list[dict]:
    p = demo_users_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def bootstrap(force: bool = False) -> dict:
    s = get_settings()
    stats: dict = {}
    has_companies = db.query_one("SELECT COUNT(*) AS n FROM companies")["n"]
    if force or not has_companies:
        src = data_source_dir()
        profiles = build_profiles(src, limit=s.max_companies)
        save_profiles(profiles)
        stats["companies"] = len(profiles)
        links = src / "df_Fid_Uid_link_hack.csv"
        stats["user_links"] = load_user_links(links) if links.exists() else 0
        pick_demo_users()
    has_events = db.query_one("SELECT COUNT(*) AS n FROM events")["n"]
    if force or not has_events:
        stats["ingest"] = run_ingest(only=["demo_seed"])
    users = load_demo_users()
    if not users or not db.query_one("SELECT 1 FROM user_links WHERE fid=?", (users[0]["user_id"],)):
        pick_demo_users()
    return stats
