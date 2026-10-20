"""Бенчмарк разрешённых моделей на задачах сервиса → обоснованный роутинг (config/models.yaml).

1. Классификация новостей: 12 размеченных новостей (data/eval/classification_labels.jsonl):
   F1 по факторам, точность направления, доля валидного JSON с первой попытки, латентность, стоимость.
   Базовая линия — классификатор на правилах (без LLM).
2. Интерпретация: N кейсов «персона × событие» → черновик инсайта каждой моделью:
   проверки правил-критика, оценки LLM-судьи (DeepSeek; его собственный результат судит GLM), латентность, стоимость.

  python scripts/benchmark_models.py [--cases 3] [--max-rub 60]
Результат: docs/llm/BENCHMARK.md, docs/llm/benchmark.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import prompts
from app.agent.llm_schemas import ClassificationOut, CriticOut, InsightDraftOut
from app.agent.orchestrator import _context_for_llm, _parse_insight, _rule_plan, execute
from app.agent.rules import critique_rules
from app.agent.tools import AgentContext
from app.agent.trace import Tracer
from app.bootstrap import bootstrap, load_demo_users
from app.config import REPO_ROOT, load_yaml
from app.data.profiles import get_profile
from app.data.sensitivity import compute_exposures
from app.evaluation import classification_scores, load_eval_news
from app.llm.client import LLMUnavailable, get_llm, llm_priority
from app.news.classify import classification_messages, classify_rules
from app.news.ingest import list_events
from app.ranking.scorer import rank_feed, score_event
from app.security.guard import UNTRUSTED_NOTICE

OUT = REPO_ROOT / "docs" / "llm"
TEXT_MODELS = ["gemma", "qwen", "glm", "deepseek", "kimi"]


class Budget:
    def __init__(self, max_rub: float):
        self.max_rub, self.spent = max_rub, 0.0

    def add(self, rub: float) -> None:
        self.spent += rub
        if self.spent >= self.max_rub:
            raise LLMUnavailable(f"Лимит бенчмарка {self.max_rub} ₽ исчерпан")


def bench_classification(budget: Budget) -> dict:
    llm = get_llm()
    items, labels, _ = load_eval_news()
    items = {k: v for k, v in items.items() if k in labels}
    factors_cfg = load_yaml("factors.yaml")["factors"]
    results = {"rules": {"scores": classification_scores(
        {nid: {h.factor: h.direction for h in classify_rules(it.title, it.text).factors} for nid, it in items.items()}, labels),
        "json_first_try": 1.0, "latency_s": 0.0, "cost_rub": 0.0}}
    for key in TEXT_MODELS:
        preds, lat, cost, first_ok, errors = {}, [], 0.0, 0, 0
        for nid, it in items.items():
            msgs, _ = classification_messages(it)
            t0 = time.perf_counter()
            try:
                data, res = llm.complete_json("classify_news", msgs, ClassificationOut, max_tokens=800, model_key=key)
            except LLMUnavailable:
                raise
            except Exception:
                errors += 1
                continue
            lat.append(time.perf_counter() - t0)
            cost += res.cost_rub
            budget.add(res.cost_rub)
            first_ok += not res.repaired
            preds[nid] = {f["factor"]: f.get("direction", "unknown") for f in data["factors"] if f.get("factor") in factors_cfg}
        results[key] = {"scores": classification_scores(preds, labels), "json_first_try": round(first_ok / len(items), 2),
                        "errors": errors, "latency_s": round(statistics.median(lat), 2) if lat else None,
                        "cost_rub": round(cost, 3)}
        print("classify", key, results[key])
    return results


def _cases(n: int) -> list[tuple]:
    events = list_events(300)
    out = []
    for u in load_demo_users()[:n]:
        p = get_profile(u["primary_company"])
        top = rank_feed(events, p, u["role"], limit=1)
        if top:
            out.append((u, p, top[0].event))
    return out


def bench_interpretation(n_cases: int, budget: Budget) -> dict:
    llm = get_llm()
    results: dict = {k: {"checks_passed": [], "judge": [], "latency_s": [], "cost_rub": 0.0, "errors": 0}
                     for k in ["glm", "qwen", "deepseek", "kimi"]}
    for u, p, ev in _cases(n_cases):
        exposures = compute_exposures(p, u["role"])
        ctx = AgentContext(event=ev, profile=p, role=u["role"], exposures=exposures, scored=score_event(ev, p, exposures))
        execute(ctx, _rule_plan(ctx), Tracer())  # одинаковый контекст для всех моделей
        context = _context_for_llm(ctx)
        msg = [{"role": "system", "content": prompts.INTERPRET_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
               {"role": "user", "content": context}]
        for key in results:
            t0 = time.perf_counter()
            try:
                data, res = llm.complete_json("interpret", msg, InsightDraftOut, max_tokens=2200, model_key=key)
                ins = _parse_insight(data, ctx, f"llm:{res.model}")
            except LLMUnavailable:
                raise
            except Exception:
                results[key]["errors"] += 1
                continue
            results[key]["latency_s"].append(time.perf_counter() - t0)
            results[key]["cost_rub"] += res.cost_rub
            budget.add(res.cost_rub)
            checks, _ = critique_rules(ins, ctx)
            results[key]["checks_passed"].append(sum(c.passed for c in checks) / len(checks))
            judge = "glm" if key == "deepseek" else "deepseek"
            jmsg = [{"role": "system", "content": prompts.CRITIC_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
                    {"role": "user", "content": context + "\n<draft>\n" + ins.model_dump_json() + "\n</draft>"}]
            try:
                jd, jres = llm.complete_json("judge", jmsg, CriticOut, max_tokens=700, model_key=judge)
                budget.add(jres.cost_rub)
                if jd["scores"]:
                    results[key]["judge"].append(statistics.mean(jd["scores"].values()))
            except (ValueError, KeyError):
                pass
            print("interpret", u["label"], key, results[key]["checks_passed"][-1:], results[key]["judge"][-1:])
    summary = {}
    for k, r in results.items():
        summary[k] = {"checks_passed": round(statistics.mean(r["checks_passed"]), 3) if r["checks_passed"] else None,
                      "judge_mean": round(statistics.mean(r["judge"]), 2) if r["judge"] else None,
                      "latency_s": round(statistics.median(r["latency_s"]), 1) if r["latency_s"] else None,
                      "cost_rub_per_case": round(r["cost_rub"] / max(1, len(r["latency_s"])), 3), "errors": r["errors"]}
    return summary


def write_report(cls: dict, interp: dict, budget: Budget) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = {"run_at": datetime.now(UTC).isoformat(), "classification": cls, "interpretation": interp,
            "spent_rub": round(budget.spent, 2), "models": {k: m["id"] for k, m in get_llm().cfg["models"].items()}}
    (OUT / "benchmark.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Бенчмарк разрешённых моделей на задачах сервиса", "", f"Прогон: {data['run_at']} · потрачено {data['spent_rub']} ₽", "",
             "## Классификация новостей (12 размеченных новостей)", "",
             "| Модель | F1 факторов | Точность направления | JSON с 1-й попытки | Латентность (медиана), с | Стоимость, ₽ |",
             "|---|---|---|---|---|---|"]
    for k, r in cls.items():
        s = r["scores"]
        lines.append(f"| {k} | {s['f1']} | {s['direction_acc']} | {r['json_first_try']} | {r['latency_s']} | {r['cost_rub']} |")
    lines += ["", "## Интерпретация (черновик инсайта)", "",
              "| Модель | Проверки самоконтроля | Оценка судьи (1–5) | Латентность, с | ₽ за кейс | Ошибки |", "|---|---|---|---|---|---|"]
    for k, r in interp.items():
        lines.append(f"| {k} | {r['checks_passed']} | {r['judge_mean']} | {r['latency_s']} | {r['cost_rub_per_case']} | {r['errors']} |")
    lines += ["", "Судья — DeepSeek-V4.1-Flash (собственный черновик DeepSeek оценивает GLM-5.3-Flash).",
              "Выводы и итоговый роутинг фиксируются в config/models.yaml и docs/QUALITY.md."]
    (OUT / "BENCHMARK.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3)
    ap.add_argument("--max-rub", type=float, default=60)
    ap.add_argument("--skip-interpret", action="store_true")
    a = ap.parse_args()
    if not get_llm().live:
        sys.exit("Нужен LLM_API_KEY (режим live)")
    bootstrap()
    budget = Budget(a.max_rub)
    cls, interp = {}, {}
    with llm_priority("background"):
        try:
            cls = bench_classification(budget)
            if not a.skip_interpret:
                interp = bench_interpretation(a.cases, budget)
        except LLMUnavailable as e:
            print("Остановлено:", e)
    write_report(cls, interp, budget)


if __name__ == "__main__":
    main()
