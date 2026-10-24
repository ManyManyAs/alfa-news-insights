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


def test_scenarios_carry_company_sensitivity():
    """Этап 3: рядом с «заметного влияния нет» — чувствительность компании к отклонениям от прогноза банка."""
    from app.agent.scenario import CALC_VERSION, scenarios
    from app.bootstrap import load_demo_users
    from app.data.profiles import get_profile

    rates = []
    for u in load_demo_users():
        p = get_profile(u["primary_company"])
        sc = scenarios(p, ["key_rate"])
        sens = sc[0]["sensitivity"]
        assert sc[0]["version"] == CALC_VERSION and all(s["sensitivity"] == sens for s in sc)
        assert all(x["lever"] in ("rate", "fx", "wages") and abs(x["effect_rub"]) >= 1000 for x in sens)
        rates += [x for x in sens if x["lever"] == "rate"]
    # событие о ставке двигает этот рычаг; у компаний с кредитом ставка выше прогноза — рост расходов по долгу,
    # у компаний только с депозитами — доход (знак плюс тоже верен)
    assert rates and all(x["event_lever"] for x in rates) and any(x["effect_rub"] < 0 for x in rates)


def test_bank_number_must_be_in_cited_material_and_is_relinked():
    """Судьи 24.10: «12,4%» приписано прогнозу банка, где 13,0% — число должно быть в том материале, на который ссылка."""
    from app.agent.rules import bank_number_mismatch, foreign_profile_sources, repair_rules
    from app.knowledge.bank import get_knowledge

    kb = get_knowledge()
    fc = next(c for c in kb.chunks if c.id.startswith("fc-") and "%" in c.text)
    own = {x.replace(",", ".") for x in __import__("re").findall(r"\d+(?:[.,]\d+)?", fc.text)}
    other = next(c for c in kb.chunks if c.kind == "thesis" and any(
        x.replace(",", ".") not in own for x in __import__("re").findall(r"(\d+,\d+)\s*%", c.text)))
    num = next(x for x in __import__("re").findall(r"(\d+,\d+)\s*%", other.text) if x.replace(",", ".") not in own)
    claim = Claim(text=f"Банк ожидает ставку около {num}%", kind="bank_view", sources=[f"bank:{fc.id}"])
    ins = _ins(bank_view=[claim], generated_by="llm:deepseek")
    ctx = _ctx({})
    ctx.memory["_allowed_bank"] = {f"bank:{fc.id}", f"bank:{other.id}"}
    found = bank_number_mismatch(ins, ctx)
    assert found and found[0][2] == f"bank:{other.id}"
    # ссылки на профиль вне allowed_sources
    ctx.memory["_allowed_all"] = {"profile:OKVED=41", f"bank:{fc.id}"}
    ins.why_it_matters = [Claim(text="Компания в Москве", kind="ai_inference", sources=["profile:регион"])]
    assert foreign_profile_sources(ins, ctx) == ["profile:регион"]
    ctx.profile = SimpleNamespace(okved="41.20")
    ctx.event.news[0].id = "n1"
    ctx.event.title = "Событие"
    fixed = repair_rules(ins, ctx)
    assert f"bank:{other.id}" in fixed.bank_view[0].sources
    assert fixed.why_it_matters[0].sources == ["profile:OKVED=41"]
