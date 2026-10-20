"""Снапшот результатов LLM для проверки без ключа.

Ключ организаторов перестаёт работать после приёма решений, а жюри смотрит репозиторий.
Поэтому прогон в режиме live (события, инсайты, трассировки, ответы моделей) сохраняется
в data/snapshot/*.jsonl.gz и подгружается при старте. В режиме replay сервис отдаёт
сохранённые ответы моделей, а для новых запросов (например, чужой новости) переходит
на детерминированный режим — это честно видно в UI («генерация: rules»).

  uv run python scripts/manage.py snapshot-export   # после прогона в live
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.storage import db

log = logging.getLogger(__name__)
TABLES = ["events", "news", "insights", "trace_steps", "llm_cache"]


def snapshot_dir() -> Path:
    return get_settings().data_dir / "snapshot"


def data_fingerprint() -> str:
    """Отпечаток набора компаний: снапшот применим только к тем же данным (event/company id)."""
    rows = db.query("SELECT uid FROM companies ORDER BY uid LIMIT 2000")
    n = db.query_one("SELECT COUNT(*) n FROM companies")["n"]
    h = hashlib.sha256((str(n) + ",".join(r["uid"] for r in rows)).encode()).hexdigest()[:16]
    return f"{n}:{h}"


def export_snapshot(target: Path | None = None) -> dict:
    target = target or snapshot_dir()
    target.mkdir(parents=True, exist_ok=True)
    counts = {}
    for t in TABLES:
        rows = [dict(r) for r in db.query(f"SELECT * FROM {t}")]
        with gzip.open(target / f"{t}.jsonl.gz", "wt", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        counts[t] = len(rows)
    usage = db.query_one("SELECT COALESCE(SUM(cost_rub),0) c, COALESCE(SUM(calls),0) n FROM llm_usage")
    manifest = {"created_at": datetime.now(UTC).isoformat(), "data_fingerprint": data_fingerprint(),
                "counts": counts, "llm_calls": usage["n"], "llm_cost_rub": round(usage["c"], 2)}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def import_snapshot(source: Path | None = None) -> dict | None:
    source = source or snapshot_dir()
    mf = source / "manifest.json"
    if not mf.exists():
        return None
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    if manifest.get("data_fingerprint") != data_fingerprint():
        log.warning("Снапшот сделан на других данных (%s) — пропускаем", manifest.get("data_fingerprint"))
        return None
    counts = {}
    with db.tx() as conn:
        for t in TABLES:
            p = source / f"{t}.jsonl.gz"
            if not p.exists():
                continue
            n = 0
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    cols = list(r)
                    conn.execute(f"INSERT OR IGNORE INTO {t}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                 [r[c] for c in cols])
                    n += 1
            counts[t] = n
    log.info("Снапшот загружен: %s", counts)
    return {"manifest": manifest, "imported": counts}
