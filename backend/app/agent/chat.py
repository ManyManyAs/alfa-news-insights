"""Чат по готовой аналитике (критерий 10): уточняющие вопросы, другой разрез, переход к первоисточнику."""
from __future__ import annotations

import json
from collections.abc import Iterator

from app.agent import prompts
from app.agent.schemas import Insight
from app.data.models import CompanyProfile
from app.knowledge.bank import get_knowledge
from app.llm.client import LLMUnavailable, get_llm
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted


def _fmt(claims, label: str) -> str:
    return "\n".join(f"• {c.text} [{', '.join(c.sources)}]" for c in claims) or f"{label}: нет данных."


def answer_rules(q: str, ins: Insight, profile: CompanyProfile) -> dict:
    ql = q.lower()
    if any(w in ql for w in ("источник", "первоисточ", "ссылк", "откуда")):
        lines = [f"• [{s.id}] {s.title}" + (f" — {s.url}" if s.url else "") + (f", стр. {s.page}" if s.page else "") for s in ins.sources]
        return {"answer": "Источники вывода:\n" + "\n".join(lines), "mode": "rules"}
    if any(w in ql for w in ("банк", "альфа", "аналитик", "позици")):
        return {"answer": "Позиция Альфа-Банка:\n" + _fmt(ins.bank_view, "Позиция банка"), "mode": "rules"}
    if "риск" in ql:
        return {"answer": "Вывод сервиса — риски:\n" + _fmt(ins.consequences.risks, "Риски"), "mode": "rules"}
    if any(w in ql for w in ("возможн", "шанс", "выгод")):
        return {"answer": "Вывод сервиса — возможности:\n" + _fmt(ins.consequences.opportunities, "Возможности"), "mode": "rules"}
    if any(w in ql for w in ("делать", "действ", "шаг", "рекоменд")):
        return {"answer": "Возможные действия (вывод сервиса):\n" + _fmt(ins.actions, "Действия"), "mode": "rules"}
    if any(w in ql for w in ("контрагент", "покупател", "поставщик", "разрез", "групп")):
        cps = profile.counterparties
        lines = [f"• {'Покупатель' if c.direction == 'in' else 'Поставщик'}: {c.okved_name or c.okved} — "
                 f"{c.share:.0%} потока [graph:{c.direction}:{c.okved}]" for c in cps]
        grp = ""
        if profile.group_revenue_share:
            grp = (f"\nГруппа компаний: {profile.group_size} юрлиц, "
                   f"доля компании в выручке группы — {profile.group_revenue_share:.0%}.")
        return {"answer": "Разрез по контрагентам:\n" + ("\n".join(lines) or "нет данных о платежах") + grp, "mode": "rules"}
    hits = get_knowledge().search(q, k=2)
    if hits:
        txt = "\n".join(f"• {h['text']} [bank:{h['id']}] — «{h['doc_title']}», {h['date']}" for h in hits)
        return {"answer": "Нашёл в аналитике Альфа-Банка:\n" + txt, "mode": "rules"}
    return {"answer": "В подготовленной аналитике нет ответа на этот вопрос. Попробуйте спросить про риски, действия, "
                      "позицию банка, контрагентов или источники.", "mode": "rules"}


def _messages(q_clean: str, ins: Insight, profile: CompanyProfile) -> list[dict]:
    kb_hits = get_knowledge().search(q_clean, k=3)
    ctx = {"insight": json.loads(ins.model_dump_json()), "company": profile.llm_view(), "bank_search": kb_hits}
    return [{"role": "system", "content": prompts.CHAT_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
            {"role": "user", "content": f"<context>{json.dumps(ctx, ensure_ascii=False)[:20000]}</context>\n"
                                        f"<user_question>{q_clean}</user_question>"}]


CHAT_PARAMS = {"max_tokens": 600, "temperature": 0.2}


def answer_stream(q: str, ins: Insight, profile: CompanyProfile) -> Iterator[dict]:
    """События для SSE: {"type": "meta"}, затем {"type": "delta", "text"}..., затем {"type": "done"}."""
    q_clean, flags = sanitize_untrusted(q)
    llm = get_llm()
    if llm.enabled:
        try:
            gen = llm.stream("chat", _messages(q_clean, ins, profile), **CHAT_PARAMS)
            first = next(gen)  # если ответа нет (replay-промах) — исключение до отправки meta
            yield {"type": "meta", "mode": f"llm:{llm.mode}", "security_flags": flags}
            yield {"type": "delta", "text": first}
            for chunk in gen:
                yield {"type": "delta", "text": chunk}
            yield {"type": "done"}
            return
        except (LLMUnavailable, StopIteration):
            pass
    res = answer_rules(q_clean, ins, profile)
    yield {"type": "meta", "mode": res["mode"], "security_flags": flags}
    yield {"type": "delta", "text": res["answer"]}
    yield {"type": "done"}


def answer(q: str, ins: Insight, profile: CompanyProfile) -> dict:
    meta, parts = {}, []
    for ev in answer_stream(q, ins, profile):
        if ev["type"] == "meta":
            meta = ev
        elif ev["type"] == "delta":
            parts.append(ev["text"])
    return {"answer": "".join(parts), "mode": meta.get("mode", "rules"), "security_flags": meta.get("security_flags", [])}
