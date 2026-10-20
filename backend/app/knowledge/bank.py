"""База знаний: позиция Альфа-Банка (тезисы + чанки аналитических материалов) и поиск BM25.

Тезисы (data/knowledge/bank_theses.json) адресуемы: у каждого есть doc_id, страница и цитата, поэтому
в интерфейсе позиция банка всегда отделена от выводов модели и ведёт на первоисточник.
Чанки документов (data/knowledge/docs/*.md) появляются после scripts/extract_bank_theses.py (OCR через Qwen3-VL).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from app.config import get_settings
from app.news.dedup import stems

STALE_DAYS = 365


@dataclass
class Chunk:
    id: str
    doc_id: str
    text: str
    page: int | None = None
    factor: str | None = None
    kind: str = "chunk"  # thesis | chunk


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(map(len, docs)) / max(1, len(docs))
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, q: list[str]) -> list[float]:
        out = []
        for tf, d in zip(self.tf, self.docs, strict=False):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out


class BankKnowledge:
    def __init__(self) -> None:
        kdir = get_settings().knowledge_dir
        data = json.loads((kdir / "bank_theses.json").read_text(encoding="utf-8"))
        self.documents = {d["doc_id"]: d for d in data["documents"]}
        self.theses = data["theses"]
        for t in self.theses:
            t["stale"] = t.get("stale") or self._is_stale(t["doc_id"])
        self.chunks: list[Chunk] = [
            Chunk(id=t["id"], doc_id=t["doc_id"], text=t["thesis"], page=t.get("page"), factor=t["factor"], kind="thesis")
            for t in self.theses
        ]
        docs_dir = kdir / "docs"
        if docs_dir.exists():
            for md in sorted(docs_dir.glob("*.md")):
                doc_id = md.stem
                for i, part in enumerate(re.split(r"\n(?=## )", md.read_text(encoding="utf-8"))):
                    page_m = re.match(r"## Стр\. (\d+)", part)
                    for j, para in enumerate(p for p in part.split("\n\n") if len(p.strip()) > 80):
                        self.chunks.append(Chunk(id=f"{doc_id}:{i}:{j}", doc_id=doc_id, text=para.strip(),
                                                 page=int(page_m.group(1)) if page_m else None))
        self.bm25 = BM25([list(stems(c.text)) for c in self.chunks])

    def _is_stale(self, doc_id: str) -> bool:
        d = self.documents.get(doc_id, {}).get("date")
        return bool(d) and (date.today() - date.fromisoformat(d)).days > STALE_DAYS

    def doc(self, doc_id: str) -> dict:
        return self.documents.get(doc_id, {"doc_id": doc_id, "title": doc_id})

    def theses_for(self, factors: list[str], include_stale: bool = False) -> list[dict]:
        fs = set(factors)
        res = [t for t in self.theses if t["factor"] in fs and (include_stale or not t["stale"])]
        return sorted(res, key=lambda t: self.doc(t["doc_id"]).get("date", ""), reverse=True)

    def search(self, query: str, k: int = 5, factors: list[str] | None = None) -> list[dict]:
        q = list(stems(query))
        scores = self.bm25.scores(q)
        ranked = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in ranked:
            if scores[i] <= 0:
                break
            c = self.chunks[i]
            if factors and c.factor and c.factor not in factors:
                continue
            doc = self.doc(c.doc_id)
            out.append({"id": c.id, "doc_id": c.doc_id, "doc_title": doc.get("title"), "date": doc.get("date"),
                        "page": c.page, "text": c.text, "kind": c.kind, "score": round(scores[i], 3),
                        "stale": self._is_stale(c.doc_id)})
            if len(out) >= k:
                break
        return out


@lru_cache
def get_knowledge() -> BankKnowledge:
    return BankKnowledge()
