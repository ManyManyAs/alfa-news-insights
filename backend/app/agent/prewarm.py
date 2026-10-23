"""Прогрев разборов после сбора новостей.

После каждого сбора новостей на сервере заранее строим разборы верха ленты каждой демо-персоны (её основная роль),
чтобы пользователь и жюри открывали готовый разбор моделью за секунду, а не ждали агента.

Ограничения, чтобы прогрев не съел бюджет пользователей:
- работает только с живыми моделями (`live`); в replay и mock ничего не делает;
- приоритет «фон»: клиент LLM сам откажет, когда потрачено больше фоновой доли дневного бюджета (background_share);
- лимит рублей и времени на один проход (config/sources.yaml: prewarm_*);
- готовый разбор моделью не пересобирается; разбор на правилах пересобирается (если модель снова недоступна,
  старый разбор остаётся — см. get_or_create_insight).
"""
from __future__ import annotations

import logging
import time

from app.config import load_yaml

log = logging.getLogger(__name__)


def prewarm_top(top: int | None = None, max_rub: float | None = None, max_seconds: float | None = None) -> dict:
    from app.agent.orchestrator import cached_insight, get_or_create_insight
    from app.bootstrap import load_demo_users
    from app.data.profiles import get_profile
    from app.data.sensitivity import role_titles
    from app.llm.client import LLMUnavailable, get_llm, llm_priority
    from app.news.ingest import list_events
    from app.ranking.scorer import rank_feed

    cfg = load_yaml("sources.yaml")
    if not cfg.get("prewarm_enabled", True):
        return {"skipped": "выключено в конфиге"}
    top = int(top if top is not None else cfg.get("prewarm_top", 3))
    roles_top = int(cfg.get("prewarm_roles_top", 0))
    max_rub = float(max_rub if max_rub is not None else cfg.get("prewarm_max_rub", 20))
    max_seconds = float(max_seconds if max_seconds is not None else cfg.get("prewarm_max_minutes", 10) * 60)

    llm = get_llm()
    if not llm.live:
        return {"skipped": f"режим {llm.mode}"}
    stats = {"built": 0, "rebuilt": 0, "ready": 0, "rules": 0, "stopped": None}
    start_rub = llm.budget_status()["spent_rub"]
    deadline = time.monotonic() + max_seconds
    events = list_events(300)
    with llm_priority("background"):
        try:
            llm.model_for("interpret", "background")  # бюджет: фоновый лимит ещё не исчерпан
            users = [(u, get_profile(u["primary_company"])) for u in load_demo_users()]
            users = [(u, p) for u, p in users if p is not None]
            # сначала основная роль каждой персоны (её видят первой), потом верх ленты для остальных ролей
            plan = [(p, u.get("role"), top) for u, p in users]
            if roles_top:
                plan += [(p, r, roles_top) for u, p in users for r in role_titles() if r != u.get("role")]
            for p, role, n in plan:
                for s in rank_feed(events, p, role, limit=n):
                    hit = cached_insight(s.event.id, p.uid, role)
                    if hit and hit.generated_by != "rules":
                        stats["ready"] += 1
                        continue
                    if time.monotonic() >= deadline:
                        raise LLMUnavailable("лимит времени прохода")
                    if llm.budget_status()["spent_rub"] - start_rub >= max_rub:
                        raise LLMUnavailable(f"лимит прохода {max_rub:.0f} ₽")
                    ins = get_or_create_insight(s.event, p, role, force=bool(hit))
                    if ins.generated_by == "rules":
                        stats["rules"] += 1
                    else:
                        stats["rebuilt" if hit else "built"] += 1
        except LLMUnavailable as e:
            stats["stopped"] = str(e)
    stats["spent_rub"] = round(llm.budget_status()["spent_rub"] - start_rub, 2)
    log.info("Прогрев разборов: %s", stats)
    return stats
