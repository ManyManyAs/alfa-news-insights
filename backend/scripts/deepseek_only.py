"""Эксперимент «только DeepSeek»: может ли одна модель сама себя проверить?

Сейчас в конвейере разбора работают разные модели: план — Qwen, черновик и правка — DeepSeek, критик — GLM
(модель не проверяет собственный черновик). Здесь те же события пересобираются, когда ВСЕ шаги делает DeepSeek:
план, черновик, критика своего черновика и правка. Затем оба варианта оценивают независимые судьи GLM и Kimi
(тот же промпт и контекст, что в scripts/judges.py).

Честная оговорка: в текущем конвейере GLM — критик, поэтому как судья он может быть мягче к «своему» варианту.
Kimi не участвует ни в одном варианте — его оценку считаем главной.

  python scripts/deepseek_only.py [--limit 12] [--max-rub 80]
Запускать на копии базы (workflow «LLM live run», task=selfcheck): пересобранные разборы не должны попасть в снапшот.
Результат: docs/llm/deepseek_only.md, docs/llm/deepseek_only.json.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from judges import AXES, JUDGES, judge_one, stored_llm_insights

from app.agent.orchestrator import get_or_create_insight
from app.agent.schemas import Insight
from app.bootstrap import bootstrap
from app.config import REPO_ROOT
from app.data.profiles import get_profile
from app.llm.client import LLMUnavailable, get_llm, llm_priority
from app.news.ingest import get_event

OUT = REPO_ROOT / "docs" / "llm"
AGENT_TASKS = ["plan", "event_brief", "interpret", "critique", "revise"]
AXES_RU = {"personalization": "Персонализация", "depth": "Глубина", "grounding": "Обоснованность",
           "bank_view": "Позиция банка", "actionability": "Конкретность действий"}


def deepseek_routing(llm) -> dict:
    """Все шаги агента — DeepSeek (и запасная модель тоже он: сравнение без подмешивания других моделей)."""
    old = copy.deepcopy(llm.cfg["tasks"])
    for t in AGENT_TASKS:
        llm.cfg["tasks"][t] = {"model": "deepseek", "fallback": "deepseek"}
    return old


def mean_scores(r: dict, judge: str) -> float | None:
    return round(statistics.mean(r[judge].values()), 2) if judge in r else None


def summarize(rows: list[dict], variant: str) -> dict:
    out: dict = {"n": 0}
    for j in JUDGES:
        vals = [r[variant][j] for r in rows if j in r[variant]]
        if not vals:
            continue
        out["n"] = max(out["n"], len(vals))
        out[j] = {"overall": round(statistics.mean(statistics.mean(v.values()) for v in vals), 2),
                  "axes": {a: round(statistics.mean(v[a] for v in vals), 2) for a in AXES}}
    meta = [r[variant]["meta"] for r in rows if r[variant].get("meta")]
    if meta:
        out["quality_pass"] = round(statistics.mean(m["quality_pass"] for m in meta), 2)
        out["iterations"] = round(statistics.mean(m["iterations"] for m in meta), 2)
        sec = [m["seconds"] for m in meta if m.get("seconds") is not None]
        rub = [m["rub"] for m in meta if m.get("rub") is not None]
        if sec:
            out["seconds_p50"] = round(statistics.median(sec), 1)
        if rub:
            out["rub_mean"] = round(statistics.mean(rub), 2)
    return out


def meta_of(ins, seconds: float | None = None, rub: float | None = None) -> dict:
    q = ins.quality or []
    return {"generated_by": ins.generated_by, "iterations": ins.iterations,
            "quality_pass": round(sum(c.passed for c in q) / len(q), 2) if q else 1.0, "seconds": seconds, "rub": rub}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--max-rub", type=float, default=80.0)
    a = ap.parse_args()
    bootstrap()
    llm = get_llm()
    if not llm.live:
        print("Нужен LLM_MODE=live")
        return
    rows: list[dict] = []
    spent = [0.0]
    start = llm.budget_status()["spent_rub"]
    stop = None
    with llm_priority("background"):
        for item in stored_llm_insights(a.limit):
            ev, prof = get_event(item["event_id"]), get_profile(item["uid"])
            if ev is None or prof is None:
                continue
            row: dict = {"key": item["key"], "title": item["payload"].get("headline", "")}
            try:
                # вариант 1 — текущий конвейер: разбор, уже собранный сегодня (Qwen → DeepSeek → критик GLM)
                cur = Insight.model_validate(item["payload"])
                row["current"] = {"meta": meta_of(cur)}
                jc = judge_one(item, spent, a.max_rub)
                row["current"].update({j: jc[j] for j in JUDGES if jc and j in jc})
                # вариант 2 — всё делает DeepSeek, включая проверку своего черновика
                old = deepseek_routing(llm)
                try:
                    b0, t0 = llm.budget_status()["spent_rub"], time.monotonic()
                    ins = get_or_create_insight(ev, prof, item["role"], force=True)
                    sec, rub = round(time.monotonic() - t0, 1), round(llm.budget_status()["spent_rub"] - b0, 2)
                finally:
                    llm.cfg["tasks"] = old
                spent[0] += rub
                row["deepseek_only"] = {"meta": meta_of(ins, sec, rub)}
                if not str(ins.generated_by).startswith("llm"):
                    row["deepseek_only"]["note"] = "модель не справилась — разбор на правилах"
                else:
                    jd = judge_one({**item, "payload": json.loads(ins.model_dump_json())}, spent, a.max_rub)
                    row["deepseek_only"].update({j: jd[j] for j in JUDGES if jd and j in jd})
            except LLMUnavailable as e:
                stop = str(e)
                print("стоп:", e)
                break
            rows.append(row)
            print(row["key"], {v: {j: mean_scores(row[v], j) for j in JUDGES} for v in ("current", "deepseek_only") if v in row})
            if spent[0] >= a.max_rub:
                stop = f"лимит прогона {a.max_rub:.0f} ₽"
                break
    rows = [r for r in rows if "current" in r and "deepseek_only" in r]
    paired = [r for r in rows if all(j in r["current"] and j in r["deepseek_only"] for j in JUDGES)]
    res = {"run_at": datetime.now(UTC).isoformat(), "spent_rub": round(llm.budget_status()["spent_rub"] - start, 2), "stopped": stop,
           "n_pairs": len(paired), "current": summarize(paired, "current"), "deepseek_only": summarize(paired, "deepseek_only"),
           "deepseek_rules_fallback": sum(1 for r in rows if r["deepseek_only"].get("note")), "rows": rows}
    write_report(res)
    print("Итог:", json.dumps({k: res[k] for k in ("n_pairs", "current", "deepseek_only", "spent_rub")}, ensure_ascii=False))


def write_report(res: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "deepseek_only.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    c, d = res["current"], res["deepseek_only"]

    def num(v) -> str:
        return "—" if v is None else str(v).replace(".", ",")

    def g(s: dict, j: str, a: str | None = None) -> str:
        part = s.get(j) or {}
        return num((part.get("axes") or {}).get(a) if a else part.get("overall"))

    L = ["# Эксперимент «только DeepSeek»: может ли модель проверить сама себя", "",
         f"Прогон: {res['run_at'][:16]} UTC · пар разборов: {res['n_pairs']} · потрачено {num(res['spent_rub'])} ₽"
         + (f" · остановлено: {res['stopped']}" if res.get("stopped") else ""), "",
         "Одни и те же события, компании и роли. **Текущий конвейер:** план — Qwen, черновик и правка — DeepSeek, критик — GLM. "
         "**Только DeepSeek:** все шаги, включая проверку собственного черновика, делает DeepSeek. "
         "Оценивают независимые судьи GLM и Kimi, шкала 1–5. GLM — критик в текущем конвейере, поэтому главная оценка — Kimi: "
         "он не участвует ни в одном варианте.", "",
         "| | Текущий: Kimi | Только DeepSeek: Kimi | Текущий: GLM | Только DeepSeek: GLM |", "|---|---|---|---|---|",
         f"| **Средняя** | **{g(c, 'kimi')}** | **{g(d, 'kimi')}** | {g(c, 'glm')} | {g(d, 'glm')} |"]
    L += [f"| {AXES_RU[a]} | {g(c, 'kimi', a)} | {g(d, 'kimi', a)} | {g(c, 'glm', a)} | {g(d, 'glm', a)} |" for a in AXES]
    L += ["", f"- Самопроверки правилами пройдено: текущий {num(c.get('quality_pass'))}, только DeepSeek {num(d.get('quality_pass'))}.",
          f"- Проходов правки в среднем: текущий {num(c.get('iterations'))}, только DeepSeek {num(d.get('iterations'))}.",
          f"- Только DeepSeek: время разбора (медиана) {num(d.get('seconds_p50'))} с, стоимость {num(d.get('rub_mean'))} ₽ за разбор.",
          f"- Разборов, где DeepSeek в одиночку не справился и сервис ушёл на правила: {res['deepseek_rules_fallback']}.", "",
          "Как читать: разница меньше 0,2 балла на 10–12 разборах — в пределах шума. Решение о роутинге принимаем по Kimi.", ""]
    (OUT / "deepseek_only.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
