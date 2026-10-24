"""JSON-схема обмена между AI-компонентами и с фронтендом.

Схема отделяет: подтверждённые факты (fact), позицию Альфа-Банка (bank_view) и выводы модели
(ai_inference); каждый Claim ссылается на источники: news:<id>, bank:<thesis_id>|bank:<chunk_id>,
profile:<признак>, graph:<связь>.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# fact — подтверждено источником; assumption — прогноз/мнение из источника (не факт);
# bank_view — позиция Альфа-Банка; ai_inference — вывод модели/сервиса.
ClaimKind = Literal["fact", "assumption", "bank_view", "ai_inference"]


class Claim(BaseModel):
    text: str
    kind: ClaimKind = "ai_inference"
    sources: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    # считается при выдаче (app/agent/review.py), модель это не заполняет:
    support: list[str] = Field(default_factory=list)  # факт: издания, в тексте которых он есть
    independent: int | None = None  # факт: независимых первоисточников (перепечатки одного агентства — один)
    as_of: str | None = None  # позиция банка: дата материала
    stale: bool = False  # позиция банка: материалу больше квартала
    used: int | None = None  # позиция банка: сколько разборов опираются на этот материал


class HiddenEffect(BaseModel):
    """Неочевидное последствие (второго порядка): найдено одной моделью, подтверждено другой (app/agent/hidden.py)."""
    text: str
    effect: Literal["risk", "opportunity"] = "risk"
    chain: list[str] = Field(default_factory=list)  # шаги «событие → … → деньги компании»
    sources: list[str] = Field(default_factory=list)  # через какую связь компании (граф платежей, продукт банка)
    plausibility: int = 2  # 3 — вероятно, 2 — возможно; натянутое не сохраняется
    verified_by: str = ""  # модель-проверяющий
    check_why: str = ""


class Action(Claim):
    action_type: Literal["analysis", "hedge", "finance", "operations", "monitor", "question"] = "analysis"
    owner: str = ""  # кто делает: финдиректор, казначей, главбух…
    due: str = ""  # к какому сроку или событию: «до 23.10 (решение ЦБ)», «в течение месяца»


class ProductOffer(BaseModel):
    product: str
    why: str
    label: str = "Предложение Альфа-Банка"


class SourceRef(BaseModel):
    id: str
    type: Literal["news", "bank", "profile", "graph", "peers", "calc"]
    title: str
    url: str | None = None
    date: str | None = None
    page: int | None = None
    quote: str | None = None
    stale: bool = False


class Consequences(BaseModel):
    risks: list[Claim] = Field(default_factory=list)
    opportunities: list[Claim] = Field(default_factory=list)
    drivers: list[Claim] = Field(default_factory=list)  # что будет влиять на развитие ситуации


class ScenarioRow(BaseModel):
    lever: str
    title: str
    effect_rub: float
    formula: str = ""
    basis: str = ""
    calc: str = ""  # формула с подставленными числами: «−162,4 млн ₽ × 50% × 1,0 п.п. = −812 тыс. ₽»


class Scenario(BaseModel):
    """Сценарий на 12 месяцев. Числа считает калькулятор (app/agent/scenario.py), а не модель."""
    name: Literal["base", "negative", "positive"]
    title: str
    assumptions: str
    params: dict = Field(default_factory=dict)
    total_rub: float = 0
    share_of_revenue: float | None = None
    rows: list[ScenarioRow] = Field(default_factory=list)
    estimated: list[str] = Field(default_factory=list)  # какие параметры компании оценены по профилю
    sources: list[str] = Field(default_factory=list)
    monthly: list[float] = Field(default_factory=list)  # накопленный эффект по месяцам горизонта
    months: list[str] = Field(default_factory=list)
    start_month: int = 0  # с какого месяца горизонта действует отраслевой эффект
    version: int = 0  # версия калькулятора: старые расчёты в кэше пересчитываются
    negligible: bool = False  # заметного денежного эффекта нет — суммы не показываем
    # чувствительность компании к типовым отклонениям от прогноза банка (не прогноз по событию)
    sensitivity: list[dict] = Field(default_factory=list)
    event_levers: list[str] = Field(default_factory=list)  # какие рычаги двигает само событие


class QualityCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Insight(BaseModel):
    event_id: str
    company_uid: str
    role: str | None = None
    headline: str
    takeaway: Claim  # главный персональный вывод в 1–2 предложениях
    what_happened: list[Claim]  # 01 Что произошло (факты отдельно от предположений)
    why_it_matters: list[Claim]  # 02 Почему важно для этой компании
    consequences: Consequences  # 03 Последствия
    actions: list[Action]  # 04 Действия
    bank_view: list[Claim]  # 05 Как оценивает банк
    # 06 На чём основан вывод — sources + basis в каждом Claim
    sources: list[SourceRef]
    products: list[ProductOffer] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)  # базовый (прогноз банка) / негативный / позитивный
    what_could_change: list[Claim] = Field(default_factory=list)  # триггеры, при которых вывод изменится
    relevance: dict = Field(default_factory=dict)
    effect: str = "watch"
    quality: list[QualityCheck] = Field(default_factory=list)
    scores: dict[str, int] = Field(default_factory=dict)  # LLM-критик: оценки 1–5 по осям
    iterations: int = 1
    generated_by: str = "rules"
    trace_id: str
    data_gaps: list[str] = Field(default_factory=list)
    # Насколько событие важно именно этой компании по оценке модели: 0 — не касается, 1 — слабо, 2 — заметно,
    # 3 — существенно. Влияет на ранжирование ленты и на виджет главной (None — разбор по правилам, без оценки)
    materiality: int | None = None
    materiality_why: str = ""
    # проверяемость разбора (app/agent/review.py, при выдаче): тип события и чек-лист, цифры рядом, что нового в сюжете
    review: dict = Field(default_factory=dict)
    # публикации события на момент разбора; что добавилось после — показываем и предлагаем пересчитать
    news_seen: list[str] | None = None
    news_added: list[dict] = Field(default_factory=list)
    # неочевидные последствия: None — ещё не искали (старый разбор), [] — искали и не нашли правдоподобных
    hidden_effects: list[HiddenEffect] | None = None
    hidden_version: int = 0  # версия правил поиска (app/agent/hidden.py: HIDDEN_VERSION); старше — пересчитать
