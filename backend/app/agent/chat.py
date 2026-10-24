"""Чат по готовой аналитике (критерий 10): уточняющие вопросы, другой разрез, переход к первоисточнику."""
from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterator

from app.agent import prompts
from app.agent.schemas import Insight
from app.data.models import CompanyProfile
from app.knowledge.bank import get_knowledge
from app.llm.client import LLMUnavailable, get_llm
from app.security.guard import UNTRUSTED_NOTICE, guard_output, sanitize_untrusted

# Вопрос не по теме (стихи, погода, код…) — честный отказ без обращения к модели: чат отвечает только по разбору,
# данным компании и аналитике банка. Деловое слово в вопросе отказ отменяет («стих про ставку ЦБ» — уже по теме).
OFFTOPIC_RE = re.compile(r"(погод|анекдот|шутк|\bстих|рецепт|гороскоп|фильм|сериал|футбол|хоккей|песн"
                         r"|напиши\s+(?:код|программ|сочинени|эссе)|\bpython\b|javascript|переведи|кто\s+ты|расскажи\s+о\s+себе)", re.I)
BUSINESS_RE = re.compile(r"(компан|бизнес|ставк|курс|риск|банк|деньг|рубл|кредит|налог|пошлин|санкц|выручк|долг|поставщ|покупат|событ"
                         r"|новост|сценар|прогноз|действ|импорт|экспорт|зарплат|\bцен|лизинг|инфляц|аренд|договор)", re.I)
REFUSAL = ("Я отвечаю только по этому разбору, данным вашей компании и аналитике Альфа-Банка — на такой вопрос ответить не могу. "
           "Спросите, например, про риски, деньги на кону, позицию банка или что сделать в первую очередь.")
MONEY_RE = re.compile(r"(деньг|рубл|сколько|сумм|сценари|потер|убыт|прибыл|₽|эффект|во что обойд|стоит нам)", re.I)
# «Объясни проще» и «Для собственника» — тот же ответ другим языком и с другим фокусом
STYLE = {
    "simple": "Объясни проще: без терминов, как владельцу небольшого бизнеса без финансового образования, 3–4 коротких предложения. "
              "Если в контексте есть сумма из расчёта (scenarios) — приведи её как пример.",
    "owner": "Ответь для собственника: три коротких пункта — сколько это может стоить компании (только суммы из scenarios, "
             "иначе «заметного влияния нет»), главный риск и одно решение, которое стоит принять.",
}


def off_topic(q: str) -> bool:
    return bool(OFFTOPIC_RE.search(q)) and not BUSINESS_RE.search(q)


def _rub(v: float) -> str:
    a = abs(v)
    if a < 500:
        return "0 ₽"
    num, unit = (f"{a / 1e9:.1f}", "млрд ₽") if a >= 1e9 else (f"{a / 1e6:.1f}", "млн ₽") if a >= 1e6 else (f"{a / 1e3:.0f}", "тыс. ₽")
    return ("−" if v < 0 else "+") + num.replace(".", ",") + " " + unit


def money_answer(ins: Insight) -> str | None:
    """Суммы из калькулятора (scenario.py), без модели: по сценариям на 12 месяцев."""
    sc = [s for s in ins.scenarios if s.name in ("negative", "base", "positive")]
    if not sc:
        return None
    if all(s.negligible for s in sc):
        return "По расчёту калькулятора заметного денежного эффекта для компании нет — суммы не приводим."
    order = {"negative": 0, "base": 1, "positive": 2}
    lines = [f"• {s.title}: {_rub(s.total_rub) if s.total_rub or s.name != 'base' else '0 ₽ к плану'} за 12 месяцев"
             + (f" [{s.sources[0]}]" if s.sources else "") for s in sorted(sc, key=lambda s: order[s.name]) if not s.negligible]
    return "Эффект для компании по расчёту калькулятора (не модели):\n" + "\n".join(lines)


def _fmt(claims, label: str) -> str:
    return "\n".join(f"• {c.text} [{', '.join(c.sources)}]" for c in claims) or f"{label}: нет данных."


def styled_rules(style: str, ins: Insight) -> str:
    """«Для собственника» и «проще» без модели: те же факты разбора, короче и другим порядком."""
    sc = {s.name: s for s in ins.scenarios}
    neg, pos = sc.get("negative"), sc.get("positive")
    money = (f"негативный сценарий {_rub(neg.total_rub)}, позитивный {_rub(pos.total_rub)} за 12 месяцев (калькулятор)"
             if neg and pos and not neg.negligible else "заметного влияния на прибыль по расчёту нет")
    risk = ins.consequences.risks[0].text if ins.consequences.risks else "существенных рисков разбор не нашёл"
    act = ins.actions[0].text if ins.actions else "действий не требуется — достаточно следить за развитием"
    if style == "owner":
        return f"Для собственника, коротко:\n• Деньги: {money}.\n• Главный риск: {risk}\n• Решение: {act}"
    return f"Если просто: {ins.takeaway.text}\nЧто это значит в деньгах: {money}.\nЧто сделать: {act}"


def answer_rules(q: str, ins: Insight, profile: CompanyProfile, style: str | None = None) -> dict:
    ql = q.lower()
    if style in STYLE:
        return {"answer": styled_rules(style, ins), "mode": "rules"}
    if MONEY_RE.search(q) and (m := money_answer(ins)):
        return {"answer": m, "mode": "rules"}
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
    if any(w in ql for w in ("похож", "конкурент", "бенчмарк", "сравн")):
        from app.data.peers import peer_benchmark

        b = peer_benchmark(profile)
        if not b:
            return {"answer": "Группа похожих компаний не найдена.", "mode": "rules"}
        lines = [f"• {r['title']}: у похожих — {r['peers_share']:.0%}, у вас — {'есть' if r['has'] else 'нет'}" for r in b["products"]]
        bc = b["buyer_concentration_vs_peers"]
        conc = f"\nКонцентрация покупателей {bc}, чем у похожих." if bc else ""
        return {"answer": f"Сравнение с похожими компаниями ({b['description']}):\n" + "\n".join(lines) + conc, "mode": "rules"}
    if any(w in ql for w in ("контрагент", "покупател", "поставщик", "разрез", "групп", "отрасл")):
        mix = [f"• {'Покупатели' if m['direction'] == 'in' else 'Поставщики'}: {m['name']} — {m['share']:.0%} платежей "
               f"[graph:{m['direction']}:{m['okved2']}]" for m in profile.industry_mix]
        extra = []
        if profile.hhi_in is not None:
            extra.append(f"Концентрация покупателей (HHI): {profile.hhi_in:.2f} — "
                         + ("высокая, зависимость от немногих покупателей" if profile.hhi_in > 0.25 else "умеренная/низкая"))
        if profile.affiliates:
            extra.append(f"Связанных компаний через общих физлиц: {len(profile.affiliates)}")
        if mix:
            return {"answer": "Отраслевой разрез контрагентов (по всем платежам):\n" + "\n".join(mix)
                              + ("\n" + "\n".join(extra) if extra else ""), "mode": "rules"}
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


def _messages(q_clean: str, ins: Insight, profile: CompanyProfile, style: str | None = None) -> list[dict]:
    kb_hits = get_knowledge().search(q_clean, k=3)
    ctx = {"insight": json.loads(ins.model_dump_json()), "company": profile.llm_view(), "bank_search": kb_hits}
    system = prompts.CHAT_SYSTEM.format(notice=UNTRUSTED_NOTICE) + (f"\n{STYLE[style]}" if style in STYLE else "")
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"<context>{json.dumps(ctx, ensure_ascii=False)[:20000]}</context>\n"
                                        f"<user_question>{q_clean}</user_question>"}]


CHAT_PARAMS = {"max_tokens": 600, "temperature": 0.2}
OWN = "ваша компания"  # свой идентификатор в ответе клиенту заменяется словами
_TAIL_RE = re.compile(r"\b[UF](?:_\d*)?$")  # конец фрагмента, похожий на начало идентификатора


def _attach(q: str, ins: Insight) -> str | None:
    """К ответу про деньги фронтенд добавляет мини-график сценариев — те же суммы калькулятора, что в разборе."""
    return "scenarios" if MONEY_RE.search(q) and any(not s.negligible for s in ins.scenarios) else None


def _stream_llm(messages: list[dict], allowed: set[str]) -> Iterator[dict]:
    """Поток модели с фильтром идентификаторов; исключение — до первого фрагмента (тогда отвечают правила)."""
    gen = get_llm().stream("chat", messages, **CHAT_PARAMS)
    try:
        first = next(gen)
    except StopIteration as e:  # внутри генератора StopIteration превратился бы в RuntimeError (PEP 479)
        raise LLMUnavailable("пустой ответ модели") from e
    yield {"type": "first"}
    carry = ""
    for chunk in itertools.chain([first], gen):
        buf = carry + chunk
        m = _TAIL_RE.search(buf)
        cut = m.start() if m else len(buf)
        buf, carry = buf[:cut], buf[cut:]
        if buf:
            yield {"type": "delta", "text": guard_output(buf, allowed, OWN)}
    if carry:
        yield {"type": "delta", "text": guard_output(carry, allowed, OWN)}


def answer_stream(q: str, ins: Insight, profile: CompanyProfile, style: str | None = None) -> Iterator[dict]:
    """События для SSE: {"type": "meta"}, затем {"type": "delta", "text"}..., затем {"type": "done"}."""
    q_clean, flags = sanitize_untrusted(q)
    if off_topic(q_clean):
        yield {"type": "meta", "mode": "guard", "security_flags": flags, "offtopic": True}
        yield {"type": "delta", "text": REFUSAL}
        yield {"type": "done"}
        return
    llm = get_llm()
    attach = _attach(q_clean, ins)
    if llm.enabled:
        try:
            gen = llm.stream("chat", _messages(q_clean, ins, profile, style), **CHAT_PARAMS)
            first = next(gen)  # если ответа нет (replay-промах) — исключение до отправки meta
            yield {"type": "meta", "mode": f"llm:{llm.mode}", "security_flags": flags, "attach": attach}
            allowed, carry = {profile.uid}, ""
            for chunk in itertools.chain([first], gen):
                # идентификатор может прийти разбитым на два фрагмента («U_7» + «4328»): незаконченный хвост
                # придерживаем до следующего фрагмента, иначе фильтр увидит обрывок
                buf = carry + chunk
                m = _TAIL_RE.search(buf)
                cut = m.start() if m else len(buf)
                buf, carry = buf[:cut], buf[cut:]
                if buf:
                    yield {"type": "delta", "text": guard_output(buf, allowed, OWN)}
            if carry:
                yield {"type": "delta", "text": guard_output(carry, allowed, OWN)}
            yield {"type": "done"}
            return
        except (LLMUnavailable, StopIteration):
            pass
    res = answer_rules(q_clean, ins, profile, style)
    yield {"type": "meta", "mode": res["mode"], "security_flags": flags, "attach": attach}
    yield {"type": "delta", "text": res["answer"]}
    yield {"type": "done"}


def answer(q: str, ins: Insight, profile: CompanyProfile, style: str | None = None) -> dict:
    meta, parts = {}, []
    for ev in answer_stream(q, ins, profile, style):
        if ev["type"] == "meta":
            meta = ev
        elif ev["type"] == "delta":
            parts.append(ev["text"])
    # повторный фильтр по целому тексту: идентификатор мог прийти разбитым на два фрагмента потока
    text = guard_output("".join(parts), {profile.uid}, OWN)
    return {"answer": text, "mode": meta.get("mode", "rules"), "security_flags": meta.get("security_flags", [])}


# ------------------------------------------------------------------ чат по всей ленте (главная)
FEED_SYSTEM = """Ты — ассистент Альфа-Бизнеса. Отвечай на вопрос пользователя по ленте событий его компании.
{notice}
В контексте: события ленты с номерами (n), их важность для компании и почему они касаются компании; тезисы аналитики банка.
Используй только контекст. Ссылайся на события так: [ev:n] (номер из контекста), на тезисы банка — [bank:..].
Сначала прямо ответь (1–2 предложения), потом до 4 пунктов: событие, почему важно этой компании, что проверить.
Не завышай важность и не придумывай суммы: денежные оценки есть только в разборах событий.
Разделяй «Вывод сервиса:» и «Позиция Альфа-Банка:» (только тезисы банка из контекста). До 160 слов, без заголовков."""


def feed_answer_rules(q: str, cards: list[dict]) -> str:
    """Без модели: события, где встречаются слова вопроса, иначе — главные риски ленты по важности."""
    words = [w for w in re.findall(r"[а-яёa-z]{4,}", q.lower()) if w not in ("какие", "какой", "главное", "сейчас", "неделе", "неделю")]
    hit = [c for c in cards if any(w[:5] in (c["title"] + " " + c.get("summary", "")).lower() for w in words)]
    pick = hit[:4] or [c for c in cards if c["effect"] == "risk"][:4] or cards[:4]
    head = "По вашему вопросу в ленте:" if hit else "Главное в ленте для компании по важности:"
    lines = []
    for c in pick:
        why = c["why"].split(";")[0].strip()
        lines.append(f"• **{c['title']}** — важность {c['importance']}" + (f": {why}" if why else "") + f" [ev:{c['n']}]")
    return head + "\n" + "\n".join(lines)


def answer_feed_stream(q: str, cards: list[dict], profile: CompanyProfile, role_title: str | None) -> Iterator[dict]:
    q_clean, flags = sanitize_untrusted(q)
    events = [{"n": c["n"], "event_id": c["event_id"], "title": c["title"]} for c in cards]
    if off_topic(q_clean):
        yield {"type": "meta", "mode": "guard", "security_flags": flags, "events": events, "offtopic": True}
        yield {"type": "delta", "text": REFUSAL.replace("этому разбору", "ленте")}
        yield {"type": "done"}
        return
    llm = get_llm()
    if llm.enabled:
        ctx = {"company": profile.llm_view(), "role": role_title,
               "events": [{k: v for k, v in c.items() if k != "event_id"} for c in cards],
               "bank_search": get_knowledge().search(q_clean, k=3)}
        user_msg = f"<context>{json.dumps(ctx, ensure_ascii=False)[:20000]}</context>\n<user_question>{q_clean}</user_question>"
        msgs = [{"role": "system", "content": FEED_SYSTEM.format(notice=UNTRUSTED_NOTICE)}, {"role": "user", "content": user_msg}]
        try:
            it = _stream_llm(msgs, {profile.uid})
            next(it)
            yield {"type": "meta", "mode": f"llm:{llm.mode}", "security_flags": flags, "events": events}
            yield from it
            yield {"type": "done"}
            return
        except (LLMUnavailable, StopIteration):
            pass
    yield {"type": "meta", "mode": "rules", "security_flags": flags, "events": events}
    yield {"type": "delta", "text": feed_answer_rules(q_clean, cards)}
    yield {"type": "done"}
