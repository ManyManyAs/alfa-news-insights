"""Материалы Альфа-Банка как библиотека (этап 4 плана улучшений): документ целиком, поиск, где используется тезис.

PDF в репозиторий и на стенд не кладём (запрет распространения): показываем название, дату, страницу и дословную
цитату — этого достаточно, чтобы найти место в документе. Тексты тезисов и цифры прогнозов — из data/knowledge.
"""
from __future__ import annotations

import re

from app.knowledge.bank import get_knowledge
from app.storage import db

_usage_cache: dict = {}


def usage_map() -> dict[str, int]:
    """Сколько разборов ссылается на каждый материал банка (bank:t01, bank:fc-…). Кэш — до изменения таблицы разборов."""
    sig = db.query_one("SELECT COUNT(*) n, MAX(rowid) m FROM insights")
    key = (sig["n"], sig["m"])
    if _usage_cache.get("key") == key:
        return _usage_cache["map"]
    counts: dict[str, int] = {}
    for r in db.query("SELECT payload FROM insights"):
        refs = set(re.findall(r'"(bank:[\w.\-]+)"', r["payload"]))
        for ref in refs:
            counts[ref] = counts.get(ref, 0) + 1
    _usage_cache.update(key=key, map=counts)
    return counts


def _thesis_row(t: dict, used: dict) -> dict:
    return {"id": t["id"], "ref": f"bank:{t['id']}", "page": t.get("page"), "factor": t.get("factor"), "type": t.get("type"),
            "horizon": t.get("horizon"), "thesis": t["thesis"], "quote": t.get("quote"), "used": used.get(f"bank:{t['id']}", 0)}


def documents() -> list[dict]:
    kb, used = get_knowledge(), usage_map()
    out = []
    for d in sorted(kb.documents.values(), key=lambda x: x.get("date", ""), reverse=True):
        th = [t for t in kb.theses if t["doc_id"] == d["doc_id"]]
        fc = [r for r in kb.forecast_rows if r["doc_id"] == d["doc_id"]]
        out.append({**{k: d.get(k) for k in ("doc_id", "title", "date", "type", "file")}, "theses": len(th), "forecasts": len(fc),
                    "pages": sorted({t["page"] for t in th if t.get("page")}),
                    "used": sum(used.get(f"bank:{t['id']}", 0) for t in th), "stale": kb._is_stale(d["doc_id"])})
    return out


def document(doc_id: str) -> dict | None:
    kb, used = get_knowledge(), usage_map()
    d = kb.documents.get(doc_id)
    if not d:
        return None
    theses = sorted((_thesis_row(t, used) for t in kb.theses if t["doc_id"] == doc_id), key=lambda t: (t["page"] or 0, t["id"]))
    rows = [r for r in kb.forecast_rows if r["doc_id"] == doc_id]
    forecasts = []
    for r in sorted(rows, key=lambda r: (r["indicator"], r["period"])):
        meta = kb.indicators.get(r["indicator"], {})
        forecasts.append({"indicator": r["indicator"], "title": meta.get("title", r["indicator"]), "period": r["period"],
                          "value": r["value"], "unit": meta.get("unit", ""), "page": r.get("page"),
                          "ref": f"bank:fc-{r['indicator']}-{r['period']}"})
    return {**{k: d.get(k) for k in ("doc_id", "title", "date", "type", "file")}, "theses": theses, "forecasts": forecasts,
            "stale": kb._is_stale(doc_id), "used": sum(t["used"] for t in theses)}


def search(query: str, k: int = 6) -> list[dict]:
    """Поиск по материалам банка тем же гибридным поиском, что у агента (BM25 + факторы запроса). Без модели."""
    kb, used = get_knowledge(), usage_map()
    by_id = {t["id"]: t for t in kb.theses}
    out = []
    for r in kb.search(query, k=k):
        t = by_id.get(r["id"])
        out.append({"ref": f"bank:{r['id']}", "kind": r["kind"], "doc_id": r["doc_id"], "doc_title": r["doc_title"],
                    "date": r["date"], "page": r["page"], "text": t["thesis"] if t else r["text"],
                    "quote": (t or {}).get("quote"), "used": used.get(f"bank:{r['id']}", 0), "stale": r["stale"]})
    return out


def for_company(factors: list[str], k: int = 3) -> list[dict]:
    """Позиция банка по главным темам компании: самые свежие тезисы-прогнозы и оценки, по одному на тему."""
    kb, used = get_knowledge(), usage_map()
    out, seen_docs = [], set()
    for f in factors:
        cands = [t for t in kb.theses_for([f]) if t.get("type") in ("forecast", "assessment", "risk")]
        cands.sort(key=lambda t: kb.doc(t["doc_id"]).get("date", ""), reverse=True)
        pick = next((t for t in cands if t["doc_id"] not in seen_docs), cands[0] if cands else None)
        if pick and all(x["id"] != pick["id"] for x in out):
            doc = kb.doc(pick["doc_id"])
            out.append({**_thesis_row(pick, used), "doc_id": pick["doc_id"], "doc_title": doc.get("title"), "date": doc.get("date")})
            seen_docs.add(pick["doc_id"])
        if len(out) >= k:
            break
    return out


_PCT = r"(\d+(?:[.,]\d+)?)\s?%"


def rate_market() -> dict:
    """Ставка: прогноз банка и рыночные ставки из материалов банка (доходности ОФЗ и корпоративных облигаций).

    Числа берутся из текста тезисов по ссылке (без ручного ввода), поэтому совпадают с тем, что видит агент."""
    kb = get_knowledge()
    by_id = {t["id"]: t for t in kb.theses}
    pts = (kb.rate_path or {}).get("points") or []
    def ref(label: str) -> str | None:
        m = re.search(r"(\d{2})$", label)
        f = kb.forecast("key_rate_end", f"20{m.group(1)}") if m else None
        return (f or {}).get("ref")

    bank = [{"label": f"Ключевая ставка — {p[0]}", "value": p[1], "ref": ref(p[0]), "kind": "bank"} for p in pts]
    market = []
    t20 = by_id.get("t20", {}).get("thesis", "")
    if m := re.search(r"14-летн\w* выпуск\w*\s*[—-]\s*" + _PCT, t20):
        market.append({"label": "ОФЗ, 14 лет", "value": float(m.group(1).replace(",", ".")), "ref": "bank:t20", "kind": "market"})
    t56 = by_id.get("t56", {}).get("thesis", "")
    for rating in ("AAA", "AA", "A"):
        if m := re.search(r"~?" + _PCT + rf"\s*для\s*(?:рейтинга\s*)?{rating}\b", t56):
            market.append({"label": f"Корпоративные облигации, {rating}", "value": float(m.group(1).replace(",", ".")),
                           "ref": "bank:t56", "kind": "market"})
    return {"bank": bank, "market": market, "date": (kb.rate_path or {}).get("date"),
            "note": "Рыночные ставки выше ключевой: инвесторы закладывают высокую стоимость денег надолго, а заёмщики "
                    "ниже AAA платят заметно больше ключевой (по материалам банка)."}


def pipeline_stats() -> dict:
    kb = get_knowledge()
    return {"documents": len(kb.documents), "theses": len(kb.theses), "forecasts": len(kb.forecast_rows),
            "indicators": len({r["indicator"] for r in kb.forecast_rows}),
            "pages": len({(t["doc_id"], t.get("page")) for t in kb.theses if t.get("page")})}

