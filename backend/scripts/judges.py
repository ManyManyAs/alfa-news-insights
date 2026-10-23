"""Два независимых LLM-судьи (GLM и Kimi) на готовых LLM-разборах → согласие судей.

Зачем: одна модель-судья может систематически завышать или занижать оценки. Если две разные модели
ставят близкие баллы по тем же осям, оценке можно доверять больше; оси с большим расхождением
показывают, где критерий сформулирован размыто.

Судьи видят тот же контекст, что и критик в конвейере (план инструментов на правилах → те же данные),
и тот же промпт CRITIC_SYSTEM. Автор разборов — DeepSeek, поэтому ни один из судей не оценивает свой текст.

  python scripts/judges.py [--limit 20] [--max-rub 30]
Результат: docs/llm/judges.md, docs/llm/judges.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import prompts
from app.agent.llm_schemas import CriticOut
from app.agent.orchestrator import _context_for_llm, _rule_plan, execute
from app.agent.schemas import Insight
from app.agent.tools import AgentContext
from app.agent.trace import Tracer
from app.bootstrap import bootstrap
from app.config import REPO_ROOT
from app.data.profiles import get_profile
from app.data.sensitivity import compute_exposures
from app.llm.client import LLMUnavailable, get_llm, llm_priority
from app.news.ingest import get_event
from app.ranking.scorer import score_event
from app.security.guard import UNTRUSTED_NOTICE
from app.storage import db

OUT = REPO_ROOT / "docs" / "llm"
JUDGES = ["glm", "kimi"]
AXES = ["personalization", "depth", "grounding", "bank_view", "actionability"]
EXCLUDE = {"scenarios", "what_could_change", "quality", "scores", "relevance", "sources", "trace_id"}


def stored_llm_insights(limit: int) -> list[dict]:
    rows = db.query("SELECT key, event_id, company_uid, role, payload FROM insights ORDER BY key")
    out = []
    for r in rows:
        p = json.loads(r["payload"])
        by = str(p.get("generated_by", ""))
        if by.startswith("llm") and not any(j in by for j in ("glm", "kimi")):  # судья не оценивает свой текст
            out.append({"key": r["key"], "event_id": r["event_id"], "uid": r["company_uid"], "role": r["role"], "payload": p})
    return out[:limit]


def judge_one(item: dict, budget: list[float], max_rub: float) -> dict | None:
    ev, prof = get_event(item["event_id"]), get_profile(item["uid"])
    if ev is None or prof is None:
        return None
    exposures = compute_exposures(prof, item["role"])
    ctx = AgentContext(event=ev, profile=prof, role=item["role"], exposures=exposures,
                       scored=score_event(ev, prof, exposures))
    execute(ctx, _rule_plan(ctx), Tracer())
    ins = Insight.model_validate(item["payload"])
    msg = [{"role": "system", "content": prompts.CRITIC_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
           {"role": "user", "content": _context_for_llm(ctx) + "\n<draft>\n" + ins.model_dump_json(exclude=EXCLUDE)
            + "\n</draft>"}]
    res_by: dict = {"key": item["key"], "title": ins.headline, "materiality": ins.materiality}
    llm = get_llm()
    for j in JUDGES:
        if budget[0] >= max_rub:
            raise LLMUnavailable(f"Лимит прогона {max_rub} ₽ исчерпан")
        try:
            raw, res = llm.complete_json("judge", msg, None, max_tokens=1600, model_key=j)
            budget[0] += res.cost_rub
            jd = CriticOut.model_validate({"issues": raw} if isinstance(raw, list) else raw).model_dump()
            if jd["scores"] and all(a in jd["scores"] for a in AXES):
                res_by[j] = {a: int(jd["scores"][a]) for a in AXES}
                res_by[j + "_issues"] = jd["issues"][:3]
        except LLMUnavailable as e:
            if "Лимит" in str(e) or "бюджет" in str(e).lower():
                raise
        except ValueError:
            pass
    return res_by


def pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or statistics.pstdev(x) == 0 or statistics.pstdev(y) == 0:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / len(x)
    return round(cov / (statistics.pstdev(x) * statistics.pstdev(y)), 2)


def agreement(rows: list[dict]) -> dict:
    both = [r for r in rows if all(j in r for j in JUDGES)]
    out: dict = {"n": len(both), "axes": {}}
    for a in AXES:
        g = [r["glm"][a] for r in both]
        k = [r["kimi"][a] for r in both]
        if not both:
            continue
        diffs = [abs(x - y) for x, y in zip(g, k, strict=True)]
        out["axes"][a] = {"glm": round(statistics.mean(g), 2), "kimi": round(statistics.mean(k), 2),
                          "mean_abs_diff": round(statistics.mean(diffs), 2),
                          "within_1": round(sum(d <= 1 for d in diffs) / len(diffs), 2), "exact": round(sum(d == 0 for d in diffs) / len(diffs), 2)}
    if both:
        gm = [statistics.mean(r["glm"].values()) for r in both]
        km = [statistics.mean(r["kimi"].values()) for r in both]
        out["overall"] = {"glm": round(statistics.mean(gm), 2), "kimi": round(statistics.mean(km), 2),
                          "mean_abs_diff": round(statistics.mean(abs(a - b) for a, b in zip(gm, km, strict=True)), 2),
                          "pearson": pearson(gm, km)}
        out["disagreements"] = [{"key": r["key"], "title": r["title"], "glm": round(g, 2), "kimi": round(k, 2)}
                                for r, g, k in zip(both, gm, km, strict=True) if abs(g - k) >= 1.0]
    return out


def write_report(rows: list[dict], agr: dict, spent: float) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = {"run_at": datetime.now(UTC).isoformat(), "spent_rub": round(spent, 2), "agreement": agr, "rows": rows}
    (OUT / "judges.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Два LLM-судьи: согласие оценок", "",
             f"Прогон: {data['run_at'][:16]} UTC · разборов с оценками обоих судей: {agr['n']} · потрачено {data['spent_rub']} ₽", "",
             "Судьи — GLM и Kimi (автор разборов — DeepSeek), одинаковый контекст и промпт критика, шкала 1–5.", ""]
    if agr.get("overall"):
        o = agr["overall"]
        lines += [f"**Итог:** средняя оценка GLM {o['glm']}, Kimi {o['kimi']}; среднее расхождение {o['mean_abs_diff']} балла; "
                  f"корреляция средних по разборам {o['pearson'] if o['pearson'] is not None else '—'}.", "",
                  "| Ось | GLM | Kimi | Ср. |Δ| | Совпало ±1 | Точно |", "|---|---|---|---|---|---|"]
        for a, s in agr["axes"].items():
            lines.append(f"| {a} | {s['glm']} | {s['kimi']} | {s['mean_abs_diff']} | {s['within_1']:.0%} | {s['exact']:.0%} |")
        if agr.get("disagreements"):
            lines += ["", "Разборы, где судьи разошлись на балл и больше (средняя по осям):", ""]
            lines += [f"- {d['title']} — GLM {d['glm']}, Kimi {d['kimi']}" for d in agr["disagreements"]]
    else:
        lines.append("Нет разборов с оценками обоих судей (нет LLM-разборов в базе или исчерпан лимит).")
    lines += ["", "Как читать: расхождение ≤ 0,5 и совпадение ±1 ≥ 80% — судьям можно доверять как метрике; "
              "ось с большим расхождением — кандидат на уточнение критерия в промпте. "
              "Калибровка по людям — data/eval/human_review.csv (см. docs/QUALITY.md)."]
    (OUT / "judges.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--max-rub", type=float, default=30.0)
    a = ap.parse_args()
    bootstrap()
    rows: list[dict] = []
    spent = [0.0]
    with llm_priority("background"):
        for item in stored_llm_insights(a.limit):
            try:
                r = judge_one(item, spent, a.max_rub)
            except LLMUnavailable as e:
                print("стоп:", e)
                break
            if r:
                rows.append(r)
                print(r["key"], {j: (round(statistics.mean(r[j].values()), 2) if j in r else None) for j in JUDGES})
    agr = agreement(rows)
    write_report(rows, agr, spent[0])
    print(f"Судьи: {agr.get('overall')} · потрачено {spent[0]:.2f} ₽")


if __name__ == "__main__":
    main()
