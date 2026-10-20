"""Чат по готовой аналитике (критерий 10): уточняющие вопросы, другой разрез, переход к первоисточнику."""
from __future__ import annotations

import json

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


def answer(q: str, ins: Insight, profile: CompanyProfile) -> dict:
    q_clean, flags = sanitize_untrusted(q)
    llm = get_llm()
    if not llm.enabled:
        return answer_rules(q_clean, ins, profile) | {"security_flags": flags}
    kb_hits = get_knowledge().search(q_clean, k=3)
    ctx = {"insight": json.loads(ins.model_dump_json()), "company": profile.llm_view(), "bank_search": kb_hits}
    msg = [{"role": "system", "content": prompts.CHAT_SYSTEM.format(notice=UNTRUSTED_NOTICE)},
           {"role": "user", "content": f"<context>{json.dumps(ctx, ensure_ascii=False)[:20000]}</context>\n"
                                       f"<user_question>{q_clean}</user_question>"}]
    try:
        res = llm.complete("chat", msg, max_tokens=600)
        return {"answer": res.text, "mode": f"llm:{res.model}", "security_flags": flags}
    except LLMUnavailable:
        return answer_rules(q_clean, ins, profile) | {"security_flags": flags}
