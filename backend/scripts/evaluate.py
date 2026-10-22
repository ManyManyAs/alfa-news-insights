"""Офлайн-оценка качества (критерий 08). Все метрики — одной структурой: печать и `--json` для CI.

Персонализация: P@3, nDCG@5 и «пропуск важного» (доля карточек с меткой 2, попавших в топ-10) по разметке
  data/eval/relevance_labels.csv (0 — нерелевантно, 1 — косвенно, 2 — важно для компании).
  Оценка ведётся на фиксированную дату EVAL_NOW (иначе затухание свежести меняет метрики каждый день, и проверка в CI
  нестабильна). У новости есть срок актуальности (data/eval/news_horizon.csv: решение ЦБ — до следующего заседания,
  месячная статистика — до следующего выпуска…); метка 2 устаревшей новости считается меткой 1 («была важна»). Лента: 18 демо-новостей
  из БД + 24 новости оценочного набора data/eval/news_eval.jsonl (классифицируются правилами в памяти, без записи в БД).
Склейка дублей: precision/recall на data/eval/dedup_pairs.jsonl (пересказы одной новости и «трудные» пары).
Классификация и сущности: data/eval/classification_labels.jsonl + news_eval.jsonl.
Позиция банка: recall@5 поиска по 40 запросам. Граф: доля пунктов лент демо-персон, объяснённых через контрагентов.

  uv run python scripts/evaluate.py [--insights] [--rerank] [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import bootstrap
from app.config import get_settings
from app.data.models import CompanyProfile
from app.evaluation import ndcg
from app.news.ingest import list_events
from app.news.models import Event
from app.ranking.scorer import rank_feed

# Дата, на которую размечена релевантность (переопределяется переменной EVAL_NOW)
# «Живые новости вперёд» — политика происхождения данных (демо уступает живым), а не релевантность:
# в размеченном наборе стартовые новости помечены как демо, поэтому в офлайн-оценке правило отключено (prefer_live=False)
EVAL_NOW = datetime.fromisoformat(os.environ.get("EVAL_NOW", "2026-10-21T12:00:00+03:00"))
DEFAULT_HORIZON_DAYS = 60


def horizons() -> dict[str, int]:
    path = get_settings().data_dir / "eval" / "news_horizon.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["news_id"]: int(r["horizon_days"]) for r in csv.DictReader(f, delimiter=";")}


def eval_events() -> list[Event]:
    """События БД + новости оценочного набора как события в памяти (без шума — его отсекает сбор)."""
    from app.evaluation import load_eval_news
    from app.news.classify import classify_rules
    from app.news.entities import is_noise

    events = list(list_events(500))
    items, _, ent = load_eval_news()
    for nid, it in items.items():
        if nid.startswith("ev-") and not ent.get(nid, {}).get("noise") and not is_noise(it.title, it.text):
            cls = classify_rules(it.title, it.text, it.published_at)
            cls.trust = "agency"
            events.append(Event(id=f"eval:{nid}", title=it.title, published_at=it.published_at, news=[it], classification=cls))
    return events


def profile_of(r: dict) -> CompanyProfile:
    return CompanyProfile(uid=f"EVAL_{r['case_id']}", okved=r["okved"], okved_name=r["okved_name"],
                          products=[p for p in r["products"].split("|") if p], fts="FTS_FLAG" in r["products"],
                          fts_countries=[c for c in r["fts_countries"].split("|") if c])


def golden_effects(events: list[Event]) -> dict:
    """Эталонные пары «профиль — новость» с ожидаемым эффектом: событие в топ-10 ленты и верный знак (риск/возможность)."""
    path = get_settings().data_dir / "eval" / "golden_effects.csv"
    if not path.exists():
        return {}
    cases = {}
    with open(get_settings().data_dir / "eval" / "relevance_labels.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            cases[r["case_id"]] = r
    by_news = {n.id: e for e in events for n in e.news}
    rows, ok_effect, ok_top, fails = 0, 0, 0, []
    with open(path, encoding="utf-8") as f:
        for g in csv.DictReader(f, delimiter=";"):
            ev = by_news.get(g["news_id"])
            if not ev or g["case_id"] not in cases:
                continue
            rows += 1
            r = cases[g["case_id"]]
            feed = rank_feed(events, profile_of(r), r["role"] or None, limit=10, now=EVAL_NOW, diversify=False, prefer_live=False)
            hit = next((s for s in feed if s.event.id == ev.id or (ev.story_id and s.event.story_id == ev.story_id)), None)
            ok_top += hit is not None
            eff = hit.effect if hit else None
            if eff == g["expected_effect"] or (eff == "mixed" and g["expected_effect"] in ("risk", "opportunity")):
                ok_effect += 1
            else:
                fails.append(f"{g['case_id']}/{g['news_id']}: ожидалось {g['expected_effect']}, получено {eff}")
    return {"pairs": rows, "in_top10": round(ok_top / rows, 3) if rows else None,
            "effect_acc": round(ok_effect / rows, 3) if rows else None, "fails": fails}


def relevance(events: list[Event], rerank_too: bool = False) -> dict:
    path = get_settings().data_dir / "eval" / "relevance_labels.csv"
    cases: dict[str, dict] = {}
    labels: dict[str, dict[str, int]] = defaultdict(dict)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            cases[r["case_id"]] = r
            labels[r["case_id"]][r["news_id"]] = int(r["label"])
    # Лента сворачивает сюжет в одну карточку: оцениваем «карточки» — событие вне сюжета или сюжет целиком
    # (метка сюжета — максимум меток его событий)
    news_to_card = {n.id: (e.story_id or e.id) for e in events for n in e.news}
    news_date = {n.id: n.published_at for e in events for n in e.news}
    hz = horizons()

    def stale(nid: str) -> bool:
        d = news_date.get(nid)
        if not d:
            return False
        t = datetime.fromisoformat(d.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=EVAL_NOW.tzinfo)
        return (EVAL_NOW - t).days > hz.get(nid, DEFAULT_HORIZON_DAYS)

    stale_n = 0
    per_case, p3s, ndcgs, crit_hit, crit_tot, rr_p3s, rr_ndcgs = {}, [], [], 0, 0, [], []
    for cid, r in cases.items():
        card_label: dict[str, int] = {}
        for n, lab in labels[cid].items():
            if lab == 2 and stale(n):
                lab, stale_n = 1, stale_n + 1  # «была важна», но срок актуальности прошёл
            if n in news_to_card:
                k = news_to_card[n]
                card_label[k] = max(card_label.get(k, 0), lab)
        prof = profile_of(r)
        feed = rank_feed(events, prof, r["role"] or None, limit=20, now=EVAL_NOW, prefer_live=False)
        ranked = [s.event.story_id or s.event.id for s in feed]
        rels = [card_label.get(e, 0) for e in ranked]
        p3 = sum(1 for x in rels[:3] if x > 0) / 3
        n5 = ndcg(rels, list(card_label.values()), 5)
        critical = {k for k, v in card_label.items() if v == 2}
        crit_tot += len(critical)
        crit_hit += len(critical & set(ranked[:10]))
        p3s.append(p3)
        ndcgs.append(n5)
        per_case[cid] = {"p3": round(p3, 3), "ndcg5": round(n5, 3), "top3": rels[:3],
                         "missed_critical": sorted(critical - set(ranked[:10]))}
        if rerank_too:
            from app.ranking.rerank import rerank

            rr = rerank(feed, prof, r["role"] or None)
            if rr["applied"]:
                key = {s.event.id: s.event.story_id or s.event.id for s in feed}
                rr_rels = [card_label.get(key[e], 0) for e in rr["order"]]
                rr_p3s.append(sum(1 for x in rr_rels[:3] if x > 0) / 3)
                rr_ndcgs.append(ndcg(rr_rels, list(card_label.values()), 5))
    out = {"p3": round(sum(p3s) / len(p3s), 3), "ndcg5": round(sum(ndcgs) / len(ndcgs), 3),
           "critical_recall10": round(crit_hit / crit_tot, 3) if crit_tot else None,
           "cases": len(cases), "pairs": sum(len(v) for v in labels.values()), "stale_critical": stale_n,
           "as_of": EVAL_NOW.date().isoformat(), "per_case": per_case}
    if rr_p3s:
        out["rerank"] = {"p3": round(sum(rr_p3s) / len(rr_p3s), 3), "ndcg5": round(sum(rr_ndcgs) / len(rr_ndcgs), 3),
                         "cases": len(rr_p3s)}
    return out


def dedup_scores() -> dict:
    """Склейка в событие: пересказ одной новости должен склеиваться, разные события того же дня — нет."""
    from app.evaluation import load_eval_news
    from app.news.dedup import BORDERLINE_AT, MERGE_AT, match_score
    from app.news.sources import JsonlSource

    items, _, _ = load_eval_news()
    items.update({n.id: n for n in JsonlSource({"id": "seed", "url": "data/sample/news_seed.jsonl"}).fetch()})
    path = get_settings().data_dir / "eval" / "dedup_pairs.jsonl"
    tp = fp = fn = tn = border = 0
    pairs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for p in pairs:
        a = items[p["a"]]
        b = p["b"] if isinstance(p["b"], dict) else {"title": items[p["b"]].title, "text": items[p["b"]].text}
        s = match_score(a.title, a.text, a.published_at, None, b["title"], b["text"], a.published_at, None)
        merged = s >= MERGE_AT
        border += BORDERLINE_AT <= s < MERGE_AT
        tp, fp = tp + (merged and p["same"]), fp + (merged and not p["same"])
        fn, tn = fn + (not merged and p["same"]), tn + (not merged and not p["same"])
    return {"precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "pairs": tp + fp + fn + tn, "borderline_to_arbiter": border}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--insights", action="store_true", help="также построить инсайты и посчитать долю прошедших самопроверку")
    ap.add_argument("--rerank", action="store_true", help="сравнить базовый порядок с LLM-реранком (нужен live/replay)")
    ap.add_argument("--json", type=Path, help="сохранить все метрики в JSON (для CI и экрана качества)")
    args = ap.parse_args()
    bootstrap()
    m: dict = {}
    events = eval_events()

    rel = relevance(events, args.rerank)
    m["relevance"] = rel
    for cid, c in rel["per_case"].items():
        print(f"{cid:12s} P@3={c['p3']:.2f} nDCG@5={c['ndcg5']:.2f}  top3={c['top3']}"
              + (f"  пропущено важного: {len(c['missed_critical'])}" if c["missed_critical"] else ""))
    print(f"\nСРЕДНЕЕ ({rel['cases']} профилей, {rel['pairs']} пар): P@3={rel['p3']:.3f}  nDCG@5={rel['ndcg5']:.3f}  "
          f"важное в топ-10={rel['critical_recall10']:.3f}")
    if args.rerank:
        print(f"С LLM-реранком ({rel['rerank']['cases']} профилей): P@3={rel['rerank']['p3']:.3f}  nDCG@5={rel['rerank']['ndcg5']:.3f}"
              if "rerank" in rel else "LLM-реранк не применён (нет доступа к модели)")
    g = golden_effects(events)
    if g:
        m["golden"] = g
        print(f"Эталонные пары ({g['pairs']}): в топ-10 {g['in_top10']:.3f}, верный эффект {g['effect_acc']:.3f}"
              + ("".join(f"\n  ✗ {x}" for x in g["fails"])))

    # Граф: доля пунктов топ-5 лент демо-персон, объяснённых через контрагентов/похожие компании (DoD этапа 3: ≥ 30%)
    from app.bootstrap import load_demo_users
    from app.data.profiles import get_profile

    tot = graph_hits = 0
    for u in load_demo_users():
        prof = get_profile(u["primary_company"])
        for s in rank_feed(list_events(500), prof, u["role"], limit=5, prefer_live=False):
            tot += 1
            graph_hits += any(b.startswith(("graph:", "peers:")) for r in s.reasons for b in r.get("basis", []))
    m["graph_share"] = round(graph_hits / tot, 3) if tot else None
    if tot:
        print(f"\nГраф: {graph_hits}/{tot} ({graph_hits / tot:.0%}) пунктов лент демо-персон объяснены через контрагентов/похожих")

    from app.evaluation import retrieval_recall

    rr = retrieval_recall(k=5)
    m["retrieval_recall5"] = round(rr["recall"], 3)
    print(f"\nПоиск позиции банка: recall@5={rr['recall']:.3f} ({rr['hits']}/{rr['n']}), режим: {rr['mode']}")
    for q, got in rr["misses"]:
        print(f"  промах: {q} → {got}")

    from app.evaluation import classification_scores, entity_scores, load_eval_news
    from app.news.classify import classify_rules
    from app.news.entities import is_noise

    items, lab, ent_gold = load_eval_news()
    rules = {nid: classify_rules(it.title, it.text, it.published_at) for nid, it in items.items()}
    preds = {nid: {h.factor: h.direction for h in c.factors} for nid, c in rules.items() if nid in lab}
    cs = classification_scores(preds, lab)
    m["classification"] = cs
    print(f"\nКлассификация (правила, {cs['n']} новостей): F1={cs['f1']} precision={cs['precision']} "
          f"recall={cs['recall']} направление={cs['direction_acc']}")
    ent = entity_scores({nid: {"regions": c.regions, "industries": c.industries, "effective_dates": c.effective_dates,
                               "noise": is_noise(items[nid].title, items[nid].text)} for nid, c in rules.items()}, ent_gold)
    m["entities"] = ent
    print(f"Сущности ({len(ent_gold)} новостей): регионы F1={ent['regions']['f1']}, отрасли F1={ent['industries']['f1']}, "
          f"даты вступления в силу F1={ent['effective_dates']['f1']}, фильтр шума accuracy={ent['noise_accuracy']}")

    dd = dedup_scores()
    m["dedup"] = dd
    print(f"Склейка дублей ({dd['pairs']} пар): precision={dd['precision']} recall={dd['recall']}, "
          f"пограничных (к LLM-арбитру): {dd['borderline_to_arbiter']}")

    # LLM-судья: средняя оценка разборов, построенных моделью (критик оценивает 5 осей по шкале 1–5; цель ≥ 4,2)
    from app.storage import db

    judged = [json.loads(r["payload"])["scores"] for r in db.query("SELECT payload FROM insights")
              if json.loads(r["payload"]).get("scores")]
    if judged:
        m["judge_avg"] = round(sum(sum(s.values()) / len(s) for s in judged) / len(judged), 2)
        m["judge_n"] = len(judged)
        print(f"LLM-судья: средняя оценка {m['judge_avg']:.2f} по {len(judged)} разборам")

    if args.insights:
        from app.agent.orchestrator import run_agent

        ok = total = 0
        text = (get_settings().data_dir / "eval" / "relevance_labels.csv").read_text(encoding="utf-8")
        cases = list(csv.DictReader(text.splitlines(), delimiter=";"))
        seen: set[str] = set()
        for r in cases:
            if r["case_id"] in seen or len(seen) >= 3:
                continue
            seen.add(r["case_id"])
            prof = profile_of(r)
            for s in rank_feed(events, prof, r["role"] or None, limit=3, prefer_live=False):
                ins = run_agent(s.event, prof, r["role"] or None)
                total += 1
                ok += all(c.passed for c in ins.quality)
        m["selfcheck_passed"] = round(ok / total, 3) if total else None
        print(f"Инсайтов прошли самопроверку: {ok}/{total}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        from datetime import UTC, datetime

        m["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        args.json.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
