"""Этап 1 плана улучшений: проверяемость разбора — источники фактов, дата позиции банка, чек-лист типа события,
цифры новости рядом с прогнозом банка, конкретика в объяснении."""
from datetime import date

from app.agent.review import event_kind, fact_support, kind_brief, number_pairs, review
from app.agent.rules import has_specifics
from app.agent.schemas import Action, Claim, Consequences, Insight
from app.news.models import Event, EventClassification, FactorHit, NewsItem


def _event(title: str, text: str, factors=("key_rate",), outlets=("Интерфакс",), texts=None) -> Event:
    news = [NewsItem(id=f"n{k}", source=o.lower(), source_title=o, title=title, text=(texts or [text] * len(outlets))[k])
            for k, o in enumerate(outlets)]
    return Event(id="e1", title=title, published_at="2026-10-24T10:00:00+03:00", news=news,
                 classification=EventClassification(summary=text, factors=[FactorHit(factor=f, direction="up") for f in factors]))


def _ins(**kw) -> Insight:
    base = dict(event_id="e1", company_uid="U_1", role="cfo", headline="h", takeaway=Claim(text="t", sources=["news:n0"]),
                what_happened=[], why_it_matters=[], consequences=Consequences(), actions=[], bank_view=[], sources=[],
                trace_id="x")
    base.update(kw)
    return Insight(**base)


def test_fact_confirmed_by_two_outlets_and_unconfirmed_number():
    ev = _event("ЦБ повысил ставку", "Банк России повысил ключевую ставку до 17%", outlets=("Интерфакс", "РБК"))
    assert fact_support("Ключевая ставка повышена до 17%", ev.news) == ["Интерфакс", "РБК"]
    assert fact_support("Ключевая ставка повышена до 18%", ev.news) == []  # такого числа нет ни в одном тексте


def test_event_kind_and_checklist():
    ev = _event("Совет директоров Банка России повысил ключевую ставку", "Ставка — 17%")
    assert event_kind(ev)[0] == "cb_rate"
    assert "Прогноз ставки Альфа-Банка" in kind_brief(ev)["must_answer"]
    duty = _event("Правительство повысило пошлины на ввоз оборудования", "Пошлина составит 10% с 1 января", factors=("sanctions_trade",))
    assert event_kind(duty)[0] == "duty"
    ins = _ins(what_happened=[Claim(text="Ставка повышена до 17%", kind="fact", sources=["news:n0"])],
               why_it_matters=[Claim(text="Кредит с плавающей ставкой подорожает", sources=["profile:CL_PRODUCT_LOA_MARK"])],
               bank_view=[Claim(text="Прогноз банка: 14% на конец 2026", kind="bank_view", sources=["bank:fc-key_rate_end-2026"])],
               actions=[Action(text="Пересчитать график платежей", owner="финдиректор", due="до 01.11.2026")])
    out = review(ins, ev, today=date(2026, 10, 24))
    k = out.review["kind"]
    assert k["id"] == "cb_rate" and k["passed"] == len(k["items"]) == 4
    assert out.what_happened[0].support == ["Интерфакс"]
    assert out.bank_view[0].as_of  # дата материала банка подставлена из базы знаний


def test_number_pairs_show_news_and_bank_side_by_side():
    ev = _event("ЦБ повысил ставку", "Банк России повысил ключевую ставку до 21%")
    pairs = number_pairs(ev)
    assert pairs and pairs[0]["news_value"] == 21.0 and pairs[0]["bank_ref"].startswith("bank:fc-key_rate")
    assert pairs[0]["differs"] == (abs(21.0 - pairs[0]["bank_value"]) >= 0.5)


def test_specifics_required_in_explanation():
    vague = _ins(why_it_matters=[Claim(text="Событие может повлиять на расходы компании")])
    concrete = _ins(why_it_matters=[Claim(text="37% платежей поставщиков — из строительства")])
    assert not has_specifics(vague)
    assert has_specifics(concrete)


def test_reprints_are_not_independent_confirmations():
    """Перепечатка новости агентства — один первоисточник, а не два подтверждения."""
    txt = "Банк России повысил ключевую ставку до 17% годовых"
    ev = _event("ЦБ повысил ставку", txt, outlets=("Интерфакс", "РБК"),
                texts=[txt, "Как сообщает Интерфакс, Банк России повысил ставку до 17%. Решение неожиданное для рынка."])
    from app.agent.review import independent_sources
    assert len(fact_support("Ставка повышена до 17%", ev.news)) == 2
    assert independent_sources("Ставка повышена до 17%", ev.news) == 1
    ev2 = _event("ЦБ повысил ставку", txt, outlets=("Интерфакс", "Коммерсантъ"),
                 texts=[txt, "Регулятор неожиданно поднял ставку до 17%, аналитики ждали паузы и сохранения прежнего уровня."])
    assert independent_sources("Ставка повышена до 17%", ev2.news) == 2


def test_news_added_after_insight_is_shown_and_cited():
    """К событию приклеилась публикация после разбора — разбор говорит об этом и показывает её в источниках."""
    ev = _event("ЦБ повысил ставку", "Банк России повысил ключевую ставку до 17%", outlets=("Интерфакс", "РБК"))
    ins = review(_ins(news_seen=["n0"]), ev)
    assert [x["source"] for x in ins.news_added] == ["РБК"] and any(s.id == "news:n1" for s in ins.sources)
    assert review(_ins(news_seen=["n0", "n1"]), ev).news_added == []
