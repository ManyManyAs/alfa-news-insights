"""SQLite-хранилище. Для прототипа достаточно; схема переносится в PostgreSQL без изменений логики."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    uid TEXT PRIMARY KEY,
    okved TEXT,
    group_id TEXT,
    profile TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_group ON companies(group_id);

CREATE TABLE IF NOT EXISTS peer_groups (
    key TEXT PRIMARY KEY,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS user_links (
    fid TEXT NOT NULL,
    uid TEXT NOT NULL,
    city TEXT,
    rc_name TEXT,
    PRIMARY KEY (fid, uid)
);

CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    source TEXT,
    source_title TEXT,
    url TEXT,
    title TEXT,
    text TEXT,
    published_at TEXT,
    content_hash TEXT,
    simhash TEXT,
    event_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_news_hash ON news(content_hash);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT,
    published_at TEXT,
    classification TEXT,
    news_ids TEXT,
    story_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Сюжет: цепочка связанных событий (ставка → реакция рынка → новые прогнозы)
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    title TEXT,
    factor TEXT,
    event_ids TEXT,
    first_at TEXT,
    last_at TEXT
);

-- Здоровье источников новостей: последний запуск, ошибки, сколько принесли
CREATE TABLE IF NOT EXISTS source_status (
    id TEXT PRIMARY KEY,
    title TEXT,
    type TEXT,
    trust TEXT,
    url TEXT,
    last_run TEXT,
    last_ok TEXT,
    last_error TEXT,
    failures INTEGER DEFAULT 0,
    latency_ms INTEGER,
    fetched INTEGER DEFAULT 0,
    new_events INTEGER DEFAULT 0,
    merged INTEGER DEFAULT 0,
    noise INTEGER DEFAULT 0,
    total_new_events INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_briefs (
    event_id TEXT PRIMARY KEY,
    payload TEXT,
    generated_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS insights (
    key TEXT PRIMARY KEY,
    event_id TEXT,
    company_uid TEXT,
    role TEXT,
    payload TEXT,
    trace_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trace_steps (
    trace_id TEXT,
    step_no INTEGER,
    name TEXT,
    kind TEXT,
    model TEXT,
    input TEXT,
    output TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost_rub REAL DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trace_id, step_no)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    task TEXT,
    model TEXT,
    response TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT,
    model TEXT,
    calls INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost_rub REAL DEFAULT 0,
    PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    insight_key TEXT,
    rating INTEGER,
    reason TEXT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    action TEXT,
    resource TEXT,
    allowed INTEGER,
    ts TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Колонки, добавленные после первых релизов: CREATE TABLE IF NOT EXISTS их в старую БД не добавит
MIGRATIONS = {"events": {"story_id": "TEXT"}, "news": {"minhash": "TEXT", "noise": "INTEGER DEFAULT 0"}}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_story ON events(story_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_pub ON events(published_at)")
    conn.commit()


def get_conn() -> sqlite3.Connection:
    global _conn, _conn_path
    path = get_settings().db_path
    with _lock:
        if _conn is None or _conn_path != path:
            _conn = _connect(path)
            _conn_path = path
        return _conn


def reset_connection() -> None:
    """Используется в тестах при смене DB_PATH."""
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _conn_path = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
    with _lock:
        return get_conn().execute(sql, params).fetchone()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
