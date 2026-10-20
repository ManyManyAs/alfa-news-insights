"""Первичная загрузка: профили компаний, связи пользователей, демо-новости, демо-пользователи."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import get_settings
from app.data.peers import assign_peer_groups, save_peer_groups
from app.data.profiles import build_profiles, data_quality_report, find_table, load_user_links, save_profiles
from app.news.ingest import run_ingest
from app.storage import db
from app.storage.snapshot import import_snapshot

log = logging.getLogger(__name__)
# Версия схемы профилей: увеличивать при изменении признаков компании (сервер пересоберёт профили сам)
PROFILES_VERSION = "3-graph-peers"
# Версия новостного пайплайна: при смене добираем новые демо-новости, сущности и пересобираем сюжеты
NEWS_VERSION = "4-stories"

# Демо-сценарии: подбираем реальных пользователей из данных под разные типы бизнеса
DEMO_CRITERIA = [
    ("Девелопер с кредитом", "cfo", lambda p: p["okved"].startswith("41") and "CL_PRODUCT_LOA_MARK" in p["products"]),
    ("Импортёр (Китай), оптовая торговля", "treasurer",
     lambda p: p["okved"].startswith("46") and "КИТАЙ" in [c.upper() for c in p["fts_countries"]]),
    ("IT-компания с зарплатным проектом", "accountant", lambda p: p["okved"].startswith("62") and "CL_PRODUCT_ZPP_MARK" in p["products"]),
    ("Ресторанный бизнес", "owner", lambda p: p["okved"].startswith("56")),
    ("Арендодатель недвижимости с депозитом", "cfo", lambda p: p["okved"].startswith("68") and "CL_PRODUCT_DEP_MARK" in p["products"]),
    ("Перевозчик с лизингом", "ceo", lambda p: p["okved"].startswith("49") and "CL_PRODUCT_LEA_MARK" in p["products"]),
]


def data_source_dir() -> Path:
    s = get_settings()
    if s.data_source_dir:
        return s.data_source_dir
    for d in (s.raw_dir, s.data_dir / "case"):  # data/raw — свои CSV, data/case — данные кейса в репозитории (.csv.gz)
        if find_table(d, "df_Uid_data_hack"):
            return d
    log.warning("Данные кейса не найдены — используем синтетические данные из data/sample")
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


def upgrade_news() -> dict:
    """Новые поля классификации (регионы, отрасли, даты вступления в силу, доверие) и сюжеты для уже собранных
    событий — без повторных запросов к LLM: сущности добираются правилами, разбор модели сохраняется."""
    from app.config import load_yaml
    from app.news.classify import classify_rules
    from app.news.models import EventClassification
    from app.news.stories import rebuild_stories

    trust_of = {x["id"]: x.get("trust", "media") for x in load_yaml("sources.yaml")["sources"]}
    trust_of["user_url"] = "user"
    n = 0
    for r in db.query("SELECT e.id, e.classification, n.title, n.text, n.published_at, n.source FROM events e "
                      "JOIN news n ON n.event_id = e.id WHERE e.classification IS NOT NULL GROUP BY e.id"):
        cls = EventClassification.model_validate_json(r["classification"])
        rules = classify_rules(r["title"], r["text"] or "", r["published_at"])
        cls.regions = list(dict.fromkeys(cls.regions + rules.regions))[:5]
        cls.industries = list(dict.fromkeys(cls.industries + rules.industries))[:5]
        cls.effective_dates = sorted(set(cls.effective_dates + rules.effective_dates))[:3]
        cls.trust = trust_of.get(r["source"], "user" if r["source"] in ("user", "user_text") else "media")
        with db.tx() as conn:
            conn.execute("UPDATE events SET classification=? WHERE id=?", (cls.model_dump_json(), r["id"]))
        n += 1
    stories = rebuild_stories()
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('news_version', ?)", (NEWS_VERSION,))
    return {"events": n, "stories": stories}


def bootstrap(force: bool = False) -> dict:
    s = get_settings()
    stats: dict = {}
    has_companies = db.query_one("SELECT COUNT(*) AS n FROM companies")["n"]
    ver = db.query_one("SELECT value FROM meta WHERE key='profiles_version'")
    outdated = has_companies and (not ver or ver["value"] != PROFILES_VERSION)
    if outdated:  # схема профилей поменялась (на сервере БД живёт между деплоями) — пересобираем
        log.warning("Профили устарели (версия %s → %s) — пересборка", ver["value"] if ver else None, PROFILES_VERSION)
        with db.tx() as conn:
            conn.execute("DELETE FROM insights WHERE payload LIKE '%\"generated_by\":\"rules\"%'")
        stats["profiles_rebuilt"] = True
    if force or not has_companies or outdated:
        src = data_source_dir()
        profiles = build_profiles(src, limit=s.max_companies)
        save_peer_groups(assign_peer_groups(profiles))
        save_profiles(profiles)
        with db.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('data_quality', ?)",
                         (json.dumps(data_quality_report(src), ensure_ascii=False),))
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('profiles_version', ?)", (PROFILES_VERSION,))
        stats["companies"] = len(profiles)
        links = find_table(src, "df_Fid_Uid_link_hack")
        stats["user_links"] = load_user_links(links) if links else 0
        pick_demo_users()
    has_events = db.query_one("SELECT COUNT(*) AS n FROM events")["n"]
    if force or not has_events:
        snap = import_snapshot() if s.use_snapshot else None
        if snap:
            stats["snapshot"] = snap["imported"]
        stats["ingest"] = run_ingest(only=["demo_seed"])  # после снапшота — только новые демо-новости
    nver = db.query_one("SELECT value FROM meta WHERE key='news_version'")
    if not nver or nver["value"] != NEWS_VERSION:
        if "ingest" not in stats:
            stats["ingest"] = run_ingest(only=["demo_seed"])
        stats["news_upgrade"] = upgrade_news()
    return_users = load_demo_users()
    users = return_users
    if not users or not db.query_one("SELECT 1 FROM user_links WHERE fid=?", (users[0]["user_id"],)):
        pick_demo_users()
    return stats
