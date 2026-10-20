"""Метрики качества, общие для scripts/evaluate.py и scripts/benchmark_models.py."""
from __future__ import annotations

import json
import math
from pathlib import Path

from app.config import get_settings


def load_classification_labels(path: Path | None = None) -> dict[str, dict[str, str | None]]:
    path = path or get_settings().data_dir / "eval" / "classification_labels.jsonl"
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[r["news_id"]] = r["factors"]
    return out


def classification_scores(preds: dict[str, dict[str, str]], labels: dict[str, dict[str, str | None]]) -> dict:
    """Micro-F1 по факторам и точность направления (только где эталон задаёт направление).

    preds: news_id -> {factor: direction}; учитываются первые 4 фактора предсказания (как в продукте).
    """
    tp = fp = fn = 0
    dir_ok = dir_total = 0
    for nid, gold in labels.items():
        pred = preds.get(nid, {})
        gs, ps = set(gold), set(list(pred)[:4])
        tp += len(gs & ps)
        fp += len(ps - gs)
        fn += len(gs - ps)
        for f, d in gold.items():
            if d and f in pred:
                dir_total += 1
                dir_ok += pred[f] == d
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "direction_acc": round(dir_ok / dir_total, 3) if dir_total else None, "n": len(labels)}


def retrieval_recall(k: int = 5, path: Path | None = None) -> dict:
    """recall@k поиска по аналитике банка: хотя бы один релевантный тезис/прогноз в топ-k."""
    from app.knowledge.bank import get_knowledge
    from app.knowledge.embeddings import get_embedder

    path = path or get_settings().data_dir / "eval" / "bank_retrieval.jsonl"
    kb = get_knowledge()
    hits, n, misses = 0, 0, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            got = [h["id"] for h in kb.search(r["query"], k=k)]
            n += 1
            if set(got) & set(r["relevant"]):
                hits += 1
            else:
                misses.append((r["query"], got))
    return {"recall": hits / n if n else 0.0, "hits": hits, "n": n, "misses": misses,
            "mode": "hybrid" if get_embedder() else "bm25"}


def ndcg(rels: list[int], ideal: list[int], k: int) -> float:
    dcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels[:k]))
    idcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(sorted(ideal, reverse=True)[:k]))
    return dcg / idcg if idcg else 0.0
