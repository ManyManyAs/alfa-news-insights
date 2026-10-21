"""Проверка качества в PR: сравнение офлайн-метрик с базовой линией из main.

  python scripts/quality_gate.py current.json ../docs/quality/baseline.json [--md report.md]

Печатает таблицу «метрика — база — сейчас — изменение — цель», пишет её в markdown (для комментария к PR)
и завершается с ошибкой, если метрика из списка GATED упала больше допуска. Цели — из docs/QUALITY.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# (путь в JSON, название, цель, допуск падения; None — метрика информационная, PR не блокирует)
METRICS = [
    ("relevance.p3", "Лента: P@3", 0.90, 0.05),
    ("relevance.ndcg5", "Лента: nDCG@5", 0.85, 0.05),
    ("relevance.critical_recall10", "Лента: важное в топ-10", 0.85, 0.05),
    ("retrieval_recall5", "Позиция банка: recall@5", 0.85, 0.05),
    ("classification.f1", "Классификация (правила): F1", 0.80, 0.05),
    ("classification.direction_acc", "Классификация: направление", 0.80, 0.05),
    ("entities.regions.f1", "Сущности: регионы F1", 0.90, 0.05),
    ("entities.industries.f1", "Сущности: отрасли F1", 0.90, 0.05),
    ("entities.effective_dates.f1", "Сущности: даты F1", 0.90, 0.05),
    ("entities.noise_accuracy", "Фильтр шума", 0.95, 0.05),
    ("dedup.precision", "Склейка дублей: precision", 0.90, 0.05),
    ("dedup.recall", "Склейка дублей: recall", 0.85, 0.05),
    ("graph_share", "Объяснено через граф", 0.30, None),
    ("selfcheck_passed", "Разборы прошли самопроверку", 1.0, 0.0),
]


def get(d: dict, path: str):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("current", type=Path)
    ap.add_argument("baseline", type=Path)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args()
    cur = json.loads(a.current.read_text(encoding="utf-8"))
    base = json.loads(a.baseline.read_text(encoding="utf-8")) if a.baseline.exists() else {}
    lines = ["| Метрика | База (main) | Сейчас | Изменение | Цель | |", "|---|---|---|---|---|---|"]
    failed = []
    for path, title, target, tol in METRICS:
        c, b = get(cur, path), get(base, path)
        if c is None:
            continue
        delta = None if b is None else round(c - b, 3)
        regress = tol is not None and delta is not None and delta < -tol
        if regress:
            failed.append(f"{title}: {b} → {c}")
        mark = "❌ падение" if regress else ("✅" if c >= target else "⚠️ ниже цели")
        lines.append(f"| {title} | {'—' if b is None else b} | {c} | {'—' if delta is None else f'{delta:+.3f}'} | ≥ {target} | {mark} |")
    rel = cur.get("relevance", {})
    lines.append("")
    lines.append(f"Разметка: {rel.get('pairs', '?')} пар «профиль — новость», {rel.get('cases', '?')} профилей; "
                 f"склейка — {get(cur, 'dedup.pairs')} пар. Падение больше допуска (0,05) блокирует PR.")
    report = "## Качество: офлайн-метрики против main\n\n" + "\n".join(lines)
    if failed:
        report += "\n\n**Регрессия:** " + "; ".join(failed)
    print(report)
    if a.md:
        a.md.write_text(report + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
