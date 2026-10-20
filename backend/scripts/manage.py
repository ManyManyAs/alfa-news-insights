"""Служебные команды.

  uv run python scripts/manage.py bootstrap [--force]   # пересобрать профили и демо-новости
  uv run python scripts/manage.py ingest [--source cbr] # собрать новости из источников config/sources.yaml
  uv run python scripts/manage.py warmup                # прогреть инсайты демо-пользователей
  uv run python scripts/manage.py snapshot-export       # сохранить результаты LLM в data/snapshot (для жюри)
  uv run python scripts/manage.py snapshot-import       # подгрузить снапшот в текущую БД
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import bootstrap, load_demo_users
from app.news.ingest import list_events, run_ingest


def warmup(top: int = 5) -> None:
    from app.agent.orchestrator import get_or_create_insight
    from app.data.profiles import get_profile
    from app.ranking.scorer import rank_feed

    events = list_events(300)
    for u in load_demo_users():
        p = get_profile(u["primary_company"])
        for s in rank_feed(events, p, u["role"], limit=top):
            ins = get_or_create_insight(s.event, p, u["role"])
            print(f"{u['label'][:30]:30s} | {s.event.title[:50]:50s} | {ins.generated_by} | quality={all(c.passed for c in ins.quality)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["bootstrap", "ingest", "warmup", "snapshot-export", "snapshot-import"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--source", action="append")
    a = ap.parse_args()
    if a.cmd == "bootstrap":
        print(json.dumps(bootstrap(force=a.force), ensure_ascii=False, indent=2))
    elif a.cmd == "ingest":
        print(json.dumps(run_ingest(a.source), ensure_ascii=False, indent=2))
    elif a.cmd == "warmup":
        warmup()
    elif a.cmd == "snapshot-export":
        from app.storage.snapshot import export_snapshot

        print(json.dumps(export_snapshot(), ensure_ascii=False, indent=2))
    else:
        from app.storage.snapshot import import_snapshot

        print(json.dumps(import_snapshot(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
