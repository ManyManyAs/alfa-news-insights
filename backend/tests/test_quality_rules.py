"""Проверки качества разбора по замечаниям LLM-судей (этап 2): суммы только из калькулятора, исполнитель и срок
у действий, главный вывод не повторяет заголовок."""
from datetime import date
from types import SimpleNamespace

from app.agent.rules import fill_action_meta, money_values, repeats_headline, unverified_money
from app.agent.schemas import Action, Claim, Consequences, Insight


def _ins(**kw) -> Insight:
    base = dict(event_id="e", company_uid="U_1", role="cfo", headline="ЦБ сохранил ставку 16,5%",
                takeaway=Claim(text="Проценты по кредиту не снизятся: −487 тыс. ₽ за год по оценке сервиса"),
                what_happened=[], why_it_matters=[], consequences=Consequences(), actions=[], bank_view=[],
                sources=[], products=[], scenarios=[], what_could_change=[], quality=[], scores={}, data_gaps=[])
    base.update(kw)
    return Insight.model_construct(**base)


def _ctx(calc: dict, news: str = "", role: str = "cfo"):
    return SimpleNamespace(memory={"scenario_calc": calc}, role=role,
                           event=SimpleNamespace(news=[SimpleNamespace(title="", text=news)]))


def test_money_values_parses_russian_formats():
    vals = dict(money_values("минус 487 тыс. ₽, долг 162,4 млн ₽ и 10 206 ₽, а также 1,5 млрд руб."))
    assert vals["487 тыс. ₽"] == 487_000
    assert vals["162,4 млн ₽"] == 162_400_000
    assert vals["10 206 ₽"] == 10_206
    assert vals["1,5 млрд руб"] == 1_500_000_000


def test_money_from_calculator_passes_invented_sum_is_caught():
    calc = {"scenarios": [{"total_rub": -486_912, "rows": [{"effect_rub": 10_206}]}], "defaults": {"debt_rub": 162_400_000}}
    ok = _ins(actions=[Action(text="Пересчитать график: при ставке +1 п.п. — минус 487 тыс. ₽ в год")])
    assert unverified_money(ok, _ctx(calc)) == []
    # судья Kimi: «каждый п.п. роста — это 1,7–10,9 млн ₽» — модель досчитала сама
    bad = _ins(actions=[Action(text="Каждый п.п. роста зарплат — это дополнительно 10,9 млн ₽")])
    found = unverified_money(bad, _ctx(calc))
    assert [r for _, _, r in found] == ["10,9 млн ₽"]
    # судья GLM: «+20 412 ₽» вместо «+10 206 ₽» из калькулятора
    bad2 = _ins(consequences=Consequences(drivers=[Claim(text="Позитивный сценарий: +20 412 ₽")]))
    assert unverified_money(bad2, _ctx(calc))


def test_money_quoted_from_news_is_not_flagged():
    ins = _ins(takeaway=Claim(text="Для компании это возможность при выходе в регион"),
               why_it_matters=[Claim(text="Программа на 3 млрд ₽ поддержит застройщиков региона")])
    assert unverified_money(ins, _ctx({}, news="Регион направит 3 млрд ₽ на субсидии")) == []


def test_actions_get_owner_and_concrete_due():
    ins = _ins(actions=[Action(text="Проверить ковенанты", owner="", due=""),
                        Action(text="Обсудить фиксацию ставки", owner="казначей", due="до 23.10 (решение ЦБ)")])
    fill_action_meta(ins, _ctx({}, role="accountant"), today=date(2026, 10, 24))
    assert ins.actions[0].owner == "главбух" and ins.actions[0].due == "до 23.11.2026"
    assert ins.actions[1].owner == "казначей" and ins.actions[1].due == "до 23.10 (решение ЦБ)"


def test_takeaway_repeating_headline_is_detected():
    same = _ins(headline="Дефицит кадров в общепите: вакансий +12%, зарплаты +9,6%",
                takeaway=Claim(text="Дефицит кадров в общепите: вакансий больше на 12%, зарплаты растут на 9,6%"))
    assert repeats_headline(same)
    diff = _ins(headline="Дефицит кадров в общепите: вакансий +12%, зарплаты +9,6%",
                takeaway=Claim(text="Фонд оплаты труда ресторана в 2027 году вырастет быстрее плана — бюджет стоит пересчитать"))
    assert not repeats_headline(diff)
