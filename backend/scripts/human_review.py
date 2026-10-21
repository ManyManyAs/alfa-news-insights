"""Калибровка LLM-судьи по людям: таблица из 20 разборов для ручной оценки и подсчёт согласия.

  python scripts/human_review.py export   # → data/eval/human_review.csv (20 разборов, поля для оценок пустые)
  python scripts/human_review.py score    # согласие «человек ↔ LLM-судья» по заполненным строкам → docs/quality/human_review.md

Выборка стратифицирована: разные демо-компании и все уровни существенности (materiality 0–3), чтобы в неё
попали и «сильные», и «слабые» разборы. Вопросы чек-листа — те же оси, что у судьи, плюс два итоговых.
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import bootstrap
from app.config import REPO_ROOT
from app.storage import db

CSV_PATH = REPO_ROOT / "data" / "eval" / "human_review.csv"
REPORT = REPO_ROOT / "docs" / "quality" / "human_review.md"
N = 20
# оценка 1–5, как у судьи; две последние колонки — да/нет
QUESTIONS = [
    ("h_grounding", "grounding", "Факты и числа верны и подтверждаются источниками (1–5)"),
    ("h_personalization", "personalization", "Связь с этой компанией объяснена и правдоподобна (1–5)"),
    ("h_depth", "depth", "Понятна цепочка «событие → фактор → компания → эффект» (1–5)"),
    ("h_actionability", "actionability", "Действия конкретные, их можно сделать на этой неделе (1–5)"),
    ("h_materiality_ok", None, "Существенность (0–3) оценена честно (да/нет)"),
    ("h_would_send", None, "Отправил(а) бы этот разбор клиенту без правок (да/нет)"),
]
FIELDS = ["key", "company", "role", "headline", "takeaway", "materiality", "judge_mean"] + [q[0] for q in QUESTIONS] + ["comment"]


def export() -> None:
    bootstrap()
    rows = db.query("SELECT i.key, i.role, i.payload, i.company_uid || ' · ' || json_extract(c.profile, '$.okved_name') AS name FROM insights i LEFT JOIN companies c ON c.uid = i.company_uid ORDER BY i.key")
    pool: dict = defaultdict(list)
    for r in rows:
        p = json.loads(r["payload"])
        if not str(p.get("generated_by", "")).startswith("llm") or not p.get("scores"):
            continue
        pool[(p.get("materiality"), r["name"])].append((r, p))
    picked, seen = [], set()
    # круговой обход страт: сначала по одному из каждой (существенность × компания), затем добираем
    while len(picked) < N and any(pool.values()):
        for k in sorted(pool, key=lambda x: (str(x[0]), str(x[1]))):
            if pool[k] and len(picked) < N:
                r, p = pool[k].pop(0)
                if p.get("headline") not in seen:
                    seen.add(p.get("headline"))
                    picked.append((r, p))
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
        w.writeheader()
        for r, p in picked:
            w.writerow({"key": r["key"], "company": r["name"] or "", "role": r["role"], "headline": p.get("headline", ""),
                        "takeaway": _text(p.get("takeaway")), "materiality": p.get("materiality", ""),
                        "judge_mean": round(statistics.mean(p["scores"].values()), 2)})
    print(f"{len(picked)} разборов → {CSV_PATH.relative_to(REPO_ROOT)}")


def _text(v) -> str:
    return (v.get("text") if isinstance(v, dict) else v) or ""


def _num(v: str) -> float | None:
    try:
        return float(v.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def score() -> None:
    bootstrap()
    with CSV_PATH.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f, delimiter=";") if _num(r.get("h_grounding", "")) is not None]
    if not rows:
        print("Нет заполненных строк — сначала заполните колонки h_* в", CSV_PATH.relative_to(REPO_ROOT))
        return
    payloads = {r["key"]: json.loads(r["payload"]) for r in db.query("SELECT key, payload FROM insights")}
    reviewer = os.environ.get("REVIEWER", "команда")
    lines = ["# Ручная проверка разборов и калибровка LLM-судьи", "",
             f"Проверено разборов: {len(rows)}. Кто проверял: {reviewer}.", "",
             "| Ось | Ручная проверка | LLM-судья | Ср. |Δ| | Совпало ±1 |", "|---|---|---|---|---|"]
    for col, axis, title in QUESTIONS:
        if not axis:
            continue
        pairs = [(_num(r[col]), (payloads.get(r["key"], {}).get("scores") or {}).get(axis)) for r in rows]
        pairs = [(h, j) for h, j in pairs if h is not None and j is not None]
        if pairs:
            d = [abs(h - j) for h, j in pairs]
            lines.append(f"| {title.split(' (')[0]} | {statistics.mean(h for h, _ in pairs):.2f} | "
                         f"{statistics.mean(j for _, j in pairs):.2f} | {statistics.mean(d):.2f} | {sum(x <= 1 for x in d) / len(d):.0%} |")
    for col, _, title in QUESTIONS[4:]:
        yes = [r[col].strip().lower() in ("да", "yes", "1", "+") for r in rows if r[col].strip()]
        if yes:
            lines.append(f"\n{title.split(' (')[0]}: да в {sum(yes)} из {len(yes)} ({sum(yes) / len(yes):.0%}).")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    {"export": export, "score": score}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
