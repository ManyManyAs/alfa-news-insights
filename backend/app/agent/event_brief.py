"""Двухуровневая генерация, уровень 1: разбор события, общий для всех клиентов.

Дорогая часть (что произошло, каналы влияния, сверка с позицией банка) считается один раз на событие
и кэшируется в таблице event_briefs. Персональный инсайт (уровень 2) получает этот разбор вместо
полных текстов новостей — промпт короче в несколько раз, а 500 ₽/день хватает на десятки клиентов.
"""
from __future__ import annotations

import json
import logging

from app.agent import prompts
from app.agent.llm_schemas import BankAlignment, Channel, EventBrief, SourcedText
from app.config import load_yaml
from app.knowledge.bank import get_knowledge
from app.llm.client import LLMResult, LLMUnavailable, get_llm
from app.news.models import Event
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted
from app.storage import db

log = logging.getLogger(__name__)


def brief_rules(event: Event) -> EventBrief:
    cls = event.classification
    pb = load_yaml("playbook.yaml")["factors"]
    kb = get_knowledge()
    src = [f"news:{n.id}" for n in event.news]
    channels = []
    for f in cls.factors[:4]:
        play = pb.get(f.factor, {})
        mech = {"up": play.get("risk"), "down": play.get("opportunity")}.get(f.direction) or play.get("stable") or ""
        channels.append(Channel(factor=f.factor, direction=f.direction, mechanism=mech or f.evidence or ""))
    align = []
    for t in kb.theses_for([f.factor for f in cls.factors])[:4]:
        hit = next((f for f in cls.factors if f.factor == t["factor"]), None)
        stance = "context"
        if hit and hit.direction in ("up", "down", "stable") and t["direction"] in ("up", "down", "stable"):
            stance = "agrees" if hit.direction == t["direction"] else "contradicts"
        align.append(BankAlignment(thesis_id=t["id"], stance=stance))
    return EventBrief(
        summary=cls.summary,
        key_facts=[SourcedText(text=x, sources=src[:1]) for x in cls.facts[:4]],
        forecasts=[SourcedText(text=x, sources=src[:1]) for x in cls.forecasts_or_opinions[:3]],
        channels=channels, bank_alignment=align,
    )


def _brief_context(event: Event) -> str:
    kb = get_knowledge()
    news = []
    for n in event.news:
        text, _ = sanitize_untrusted(n.text)
        news.append({"id": f"news:{n.id}", "source": n.source_title or n.source, "date": n.published_at,
                     "title": sanitize_untrusted(n.title)[0], "text": text[:3500]})
    theses = [{"ref": f"bank:{t['id']}", "factor": t["factor"], "direction": t["direction"], "thesis": t["thesis"],
               "date": kb.doc(t["doc_id"]).get("date")}
              for t in kb.theses_for([f.factor for f in event.classification.factors])[:8]]
    factors = {k: v["title"] for k, v in load_yaml("factors.yaml")["factors"].items()}
    return ("<news>\n" + json.dumps(news, ensure_ascii=False) + "\n</news>\n<bank_theses>\n"
            + json.dumps(theses, ensure_ascii=False) + "\n</bank_theses>\n<factors>\n"
            + json.dumps(factors, ensure_ascii=False) + "\n</factors>")


def get_event_brief(event: Event, force: bool = False) -> tuple[EventBrief, str, LLMResult | None]:
    """(разбор, кем построен, результат LLM для трассировки)."""
    if not force:
        row = db.query_one("SELECT payload, generated_by FROM event_briefs WHERE event_id=?", (event.id,))
        if row:
            return EventBrief.model_validate_json(row["payload"]), row["generated_by"], None
    llm = get_llm()
    brief, by, res = None, "rules", None
    if llm.enabled:
        msg = [{"role": "system", "content": prompts.EVENT_BRIEF_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
               {"role": "user", "content": _brief_context(event)}]
        try:
            data, res = llm.complete_json("event_brief", msg, EventBrief, max_tokens=1800)
            brief, by = EventBrief.model_validate(data), f"llm:{res.model}"
            valid_factors = set(load_yaml("factors.yaml")["factors"])
            brief.channels = [c for c in brief.channels if c.factor in valid_factors]
        except (LLMUnavailable, ValueError) as e:
            log.info("Разбор события по правилам: %s", e)
    if brief is None:
        brief = brief_rules(event)
    if by != "rules" or not llm.enabled or llm.live:  # в replay правила не кэшируем — вдруг появится снапшот
        with db.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO event_briefs(event_id, payload, generated_by) VALUES (?,?,?)",
                         (event.id, brief.model_dump_json(), by))
    return brief, by, res
