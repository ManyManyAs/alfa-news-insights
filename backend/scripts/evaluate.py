"""Оффлайн-оценка качества (критерий 08).

Персонализация: P@3 и nDCG@5 ленты по экспертной разметке data/eval/relevance_labels.csv
  (0 — нерелевантно, 1 — косвенно, 2 — важно для компании). Разметка расширяется командой и экспертами банка.
Обоснованность: доля инсайтов, прошедших все проверки самоконтроля (источники, числа, позиция банка, персонализация).

  uv run python scripts/evaluate.py [--insights]
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import bootstrap
from app.config import get_settings
from app.data.models import CompanyProfile
from app.news.ingest import list_events
from app.ranking.scorer import rank_feed


def ndcg(rels: list[int], ideal: list[int], k: int) -> float:
    dcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels[:k]))
    idcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(sorted(ideal, reverse=True)[:k]))
    return dcg / idcg if idcg else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--insights", action="store_true", help="также построить инсайты и посчитать долю прошедших самопроверку")
    args = ap.parse_args()
    bootstrap()
    path = get_settings().data_dir / "eval" / "relevance_labels.csv"
    cases: dict[str, dict] = {}
    labels: dict[str, dict[str, int]] = defaultdict(dict)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            cases[r["case_id"]] = r
            labels[r["case_id"]][r["news_id"]] = int(r["label"])

    events = list_events(500)
    news_to_event = {n.id: e.id for e in events for n in e.news}
    p3s, ndcgs = [], []
    for cid, r in cases.items():
        prof = CompanyProfile(uid=f"EVAL_{cid}", okved=r["okved"], okved_name=r["okved_name"],
                              products=[p for p in r["products"].split("|") if p],
                              fts="FTS_FLAG" in r["products"], fts_countries=[c for c in r["fts_countries"].split("|") if c])
        ev_label = {news_to_event[n]: lab for n, lab in labels[cid].items() if n in news_to_event}
        ranked = [s.event.id for s in rank_feed(events, prof, r["role"] or None, limit=20)]
        rels = [ev_label.get(e, 0) for e in ranked]
        p3 = sum(1 for x in rels[:3] if x > 0) / 3
        n5 = ndcg(rels, list(ev_label.values()), 5)
        p3s.append(p3)
        ndcgs.append(n5)
        print(f"{cid:12s} P@3={p3:.2f} nDCG@5={n5:.2f}  top3={[x for x in rels[:3]]}")
    print(f"\nСРЕДНЕЕ: P@3={sum(p3s) / len(p3s):.3f}  nDCG@5={sum(ndcgs) / len(ndcgs):.3f}")

    if args.insights:
        from app.agent.orchestrator import run_agent

        ok = total = 0
        for cid, r in list(cases.items())[:3]:
            prof = CompanyProfile(uid=f"EVAL_{cid}", okved=r["okved"], okved_name=r["okved_name"],
                                  products=[p for p in r["products"].split("|") if p])
            for s in rank_feed(events, prof, r["role"] or None, limit=3):
                ins = run_agent(s.event, prof, r["role"] or None)
                total += 1
                ok += all(c.passed for c in ins.quality)
        print(f"Инсайтов прошли самопроверку: {ok}/{total}")


if __name__ == "__main__":
    main()
