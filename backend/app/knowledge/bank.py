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
from app.knowledge.stemmer import tokens

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
        # Структурированные прогнозы банка (+ история пересмотров) — тоже адресуемые «чанки» bank:fc-...
        fpath = kdir / "bank_forecasts.json"
        fdata = json.loads(fpath.read_text(encoding="utf-8")) if fpath.exists() else {"indicators": {}, "forecasts": []}
        self.indicators: dict[str, dict] = fdata["indicators"]
        self.forecast_rows: list[dict] = fdata["forecasts"]
        self.rate_path = fdata.get("rate_path")
        for (ind, period) in sorted({(r["indicator"], r["period"]) for r in self.forecast_rows}):
            f = self.forecast(ind, period)
            meta = self.indicators.get(ind, {})
            self.chunks.append(Chunk(id=f"fc-{ind}-{period}", doc_id=f["doc_id"], text=f["text"], page=f["page"],
                                     factor=meta.get("factor"), kind="forecast"))
        self.bm25 = BM25([tokens(c.text) for c in self.chunks])
        self._dense = None  # ленивый индекс эмбеддингов (если включены)

    def _is_stale(self, doc_id: str) -> bool:
        d = self.documents.get(doc_id, {}).get("date")
        return bool(d) and (date.today() - date.fromisoformat(d)).days > STALE_DAYS

    def doc(self, doc_id: str) -> dict:
        return self.documents.get(doc_id, {"doc_id": doc_id, "title": doc_id})

    def theses_for(self, factors: list[str], include_stale: bool = False, query: str | None = None) -> list[dict]:
        """Тезисы по факторам. С query — сначала самые близкие к событию (BM25), затем свежие и прогнозные."""
        fs = set(factors)
        res = [t for t in self.theses if t["factor"] in fs and (include_stale or not t["stale"])]
        rel: dict[str, float] = {}
        if query and res:
            sc = self.bm25.scores(tokens(query))
            top = max(sc) or 1.0
            rel = {c.id: sc[i] / top for i, c in enumerate(self.chunks) if c.kind == "thesis"}
        type_w = {"forecast": 0.15, "risk": 0.1, "assessment": 0.05, "recommendation": 0.0}

        def key(t: dict) -> tuple:
            return (rel.get(t["id"], 0) + type_w.get(t.get("type", ""), 0), self.doc(t["doc_id"]).get("date", ""))

        return sorted(res, key=key, reverse=True)

    # --- прогнозы и эволюция позиции банка ---------------------------------
    def forecast(self, indicator: str, period: str) -> dict:
        """Последний прогноз банка по показателю + история пересмотров (от ранних к поздним)."""
        rows = sorted((r for r in self.forecast_rows if r["indicator"] == indicator and r["period"] == period),
                      key=lambda r: r["date"])
        if not rows:
            return {}
        last = rows[-1]
        meta = self.indicators.get(indicator, {"title": indicator, "unit": ""})
        history = [{"value": r["value"], "date": r["date"], "doc_id": r["doc_id"]} for r in rows]
        changes = [h for i, h in enumerate(history) if i == 0 or h["value"] != history[i - 1]["value"]]
        unit = meta.get("unit", "")
        val = _fmt(last["value"], unit)
        text = f"Прогноз Альфа-Банка: {meta['title']} в {period} г. — {val}"
        revision = None
        if len(changes) > 1:
            prev = changes[-2]
            direction = "повысил" if last["value"] > prev["value"] else "понизил"
            revision = {"from": prev["value"], "to": last["value"], "from_date": prev["date"], "to_date": last["date"],
                        "direction": "up" if direction == "повысил" else "down"}
            text += (f"; банк {direction} прогноз с {_fmt(prev['value'], unit)} ({_ru_date(prev['date'])}) "
                     f"до {val} ({_ru_date(last['date'])})")
        return {"indicator": indicator, "period": period, "title": meta["title"], "unit": unit, "value": last["value"],
                "doc_id": last["doc_id"], "date": last["date"], "page": last.get("page"), "history": history,
                "revision": revision, "factor": meta.get("factor"), "text": text.rstrip(".") + ".", "ref": f"bank:fc-{indicator}-{period}"}

    def forecasts_for(self, factors: list[str] | None = None) -> list[dict]:
        keys = sorted({(r["indicator"], r["period"]) for r in self.forecast_rows})
        out = [self.forecast(i, p) for i, p in keys]
        if factors is not None:
            fs = set(factors)
            out = [f for f in out if f["factor"] in fs]
        return out

    def revisions(self) -> list[dict]:
        return [f for f in self.forecasts_for() if f["revision"]]

    def search(self, query: str, k: int = 5, factors: list[str] | None = None) -> list[dict]:
        """Гибридный поиск: BM25 (+ эмбеддинги, если включены) с объединением рангов (RRF)."""
        scores = self.bm25.scores(tokens(query))
        # Учёт факторов запроса: тезисы того же экономического фактора поднимаются (лексика может не совпадать)
        qf = _query_factors(query)
        if qf:
            top = max(scores) or 1.0
            scores = [s + (0.35 * top if self.chunks[i].factor in qf else 0.0) for i, s in enumerate(scores)]
        ranked = [i for i in sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True) if scores[i] > 0]
        dense = self._dense_ranking(query)
        if dense:
            rrf: dict[int, float] = {}
            for lst in (ranked[:50], dense[:50]):
                for r, i in enumerate(lst):
                    rrf[i] = rrf.get(i, 0.0) + 1.0 / (60 + r)
            ranked = sorted(rrf, key=lambda i: rrf[i], reverse=True)
            scores = [rrf.get(i, 0.0) * 100 for i in range(len(self.chunks))]
        out = []
        for i in ranked:
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

    def _dense_ranking(self, query: str) -> list[int] | None:
        from app.knowledge.embeddings import get_embedder

        emb = get_embedder()
        if emb is None:
            return None
        if self._dense is None:
            self._dense = emb.index([c.text for c in self.chunks])
        return emb.rank(query, self._dense)


def _query_factors(query: str) -> set[str]:
    from app.news.classify import classify_rules  # локальный импорт: избегаем циклов

    return {h.factor for h in classify_rules(query, "").factors[:2] if h.confidence >= 0.5}


def _fmt(v: float, unit: str) -> str:
    num = f"{v:.1f}" if unit.startswith("%") else f"{v:g}"
    num = num.replace(".", ",")
    return f"{num}%{unit[1:]}" if unit.startswith("%") else f"{num} {unit}"


def _ru_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


@lru_cache
def get_knowledge() -> BankKnowledge:
    return BankKnowledge()
