"""Служебные команды.

  uv run python scripts/manage.py bootstrap [--force]   # пересобрать профили и демо-новости
  uv run python scripts/manage.py ingest [--source cbr] # собрать новости из источников config/sources.yaml
  uv run python scripts/manage.py warmup [--top 5] [--max-rub 100] [--background] [--roles]
                                                        # предрасчёт разборов событий и инсайтов демо-персон
  uv run python scripts/manage.py snapshot-export       # сохранить результаты LLM в data/snapshot (для жюри)
  uv run python scripts/manage.py snapshot-import       # подгрузить снапшот в текущую БД
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import bootstrap, load_demo_users
from app.news.ingest import list_events, run_ingest


def warmup(top: int = 5, max_rub: float | None = None, background: bool = False, roles: bool = False,
           refresh: bool = False, max_minutes: float | None = None) -> None:
    """Предрасчёт: разборы всех событий (уровень 1) + инсайты демо-персон по топ-событиям ленты (уровень 2).

    --background: приоритет «фон» — не трогает резерв бюджета под пользователей.
    --roles: дополнительно строить инсайты для всех ролей (персонализация по роли в снапшоте).
    --refresh: пересобрать все инсайты (после правки промптов); ответы моделей не из кэша.
    Без --refresh готовые разборы моделью пропускаются, а разборы на правилах пересобираются моделью.
    --max-minutes: мягкий лимит времени — прогон останавливается сам, и снапшот успевает выгрузиться
    (иначе таймаут CI убивает job и все результаты теряются).
    Порядок: сначала основная роль каждой демо-персоны, потом остальные роли — при остановке важное уже готово.
    """
    import time

    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
    from app.agent.event_brief import get_event_brief
    from app.agent.orchestrator import cached_insight, get_or_create_insight
    from app.data.profiles import get_profile
    from app.data.sensitivity import role_titles
    from app.llm.client import LLMUnavailable, get_llm, llm_priority
    from app.ranking.scorer import rank_feed

    llm = get_llm()
    if llm.live:
        try:
            llm.model_for("interpret", "background" if background else "interactive")
        except LLMUnavailable as e:
            print("Модель недоступна, предрасчёт пропущен (снапшот не меняется):", e)
            return
    start = llm.budget_status()["spent_rub"]

    def over() -> bool:
        if deadline and time.monotonic() >= deadline:
            return True
        return max_rub is not None and llm.budget_status()["spent_rub"] - start >= max_rub

    events = list_events(300)
    judged: list[float] = []
    with llm_priority("background" if background else "interactive"):
        try:
            for e in events:
                if over():
                    break
                _, by, _ = get_event_brief(e)
                print(f"brief  | {e.title[:60]:60s} | {by}")
            users = [(u, get_profile(u["primary_company"])) for u in load_demo_users()]
            plan = [(u, p, u["role"]) for u, p in users]
            if roles:
                plan += [(u, p, r) for u, p in users for r in role_titles() if r != u["role"]]
            for u, p, role in plan:
                for s in rank_feed(events, p, role, limit=top):
                    if over():
                        raise LLMUnavailable(f"лимит прогона: {max_rub} ₽ или {max_minutes} мин")
                    # без --refresh пересобираем только разборы на правилах: готовые разборы моделью не трогаем
                    hit = cached_insight(s.event.id, p.uid, role)
                    if hit and hit.generated_by != "rules" and not refresh:
                        continue
                    ins = get_or_create_insight(s.event, p, role, force=refresh or bool(hit))
                    if ins.scores:
                        judged.append(sum(ins.scores.values()) / len(ins.scores))
                    print(f"insight| {u['label'][:28]:28s} | {role:10s} | {s.event.title[:44]:44s} | {ins.generated_by} | "
                          f"quality={all(c.passed for c in ins.quality)} | scores={ins.scores}")
        except LLMUnavailable as e:
            print("Остановлено:", e)
        # «Деньги на кону»: ответ модели об отборе событий — в снапшот, чтобы и без ключа карточка была как на стенде
        try:
            from app.agent.money import money_view
            from app.main import ai_materiality, cached_rank, importance_level

            for u, p, role in plan:
                if over():
                    break
                rev = p.revenue if not p.revenue_suspicious else None
                m = money_view(p, role, cached_rank(p, role, 30), ai_materiality(p.uid, role), importance_level, cached_insight, rev)
                print(f"money  | {u['label'][:28]:28s} | {role:10s} | рычагов {len(m['levers'])} | событий {m['events_used']}"
                      f" из {m['candidates']} | ИИ: {m['ai'].get('used')}")
        except Exception as e:  # деньги — дополнительный шаг прогрева, не должен ронять снапшот
            print("money: пропущено:", type(e).__name__, str(e)[:160])
    if judged:
        print(f"LLM-судья: средняя оценка {sum(judged) / len(judged):.2f} по {len(judged)} разборам")
    print("Потрачено за прогон:", round(llm.budget_status()["spent_rub"] - start, 2), "₽")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["bootstrap", "ingest", "warmup", "snapshot-export", "snapshot-import"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--source", action="append")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--max-rub", type=float)
    ap.add_argument("--background", action="store_true")
    ap.add_argument("--roles", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="warmup: пересобрать уже готовые инсайты")
    ap.add_argument("--max-minutes", type=float, help="warmup: мягкий лимит времени прогона")
    a = ap.parse_args()
    if a.cmd == "bootstrap":
        print(json.dumps(bootstrap(force=a.force), ensure_ascii=False, indent=2))
    elif a.cmd == "ingest":
        print(json.dumps(run_ingest(a.source), ensure_ascii=False, indent=2))
    elif a.cmd == "warmup":
        warmup(a.top, a.max_rub, a.background, a.roles, a.refresh, a.max_minutes)
    elif a.cmd == "snapshot-export":
        from app.storage.snapshot import export_snapshot

        print(json.dumps(export_snapshot(), ensure_ascii=False, indent=2))
    else:
        from app.storage.snapshot import import_snapshot

        print(json.dumps(import_snapshot(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
