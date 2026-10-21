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
