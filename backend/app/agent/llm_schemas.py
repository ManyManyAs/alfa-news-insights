"""Pydantic-схемы ответов LLM: по ним валидируется и «ремонтируется» вывод каждой модели."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["up", "down", "stable", "unknown"]


class SourcedText(BaseModel):
    text: str
    sources: list[str] = Field(default_factory=list)


class Channel(BaseModel):
    """Канал влияния события на бизнес — общий для всех компаний (event-level)."""
    factor: str
    direction: Direction = "unknown"
    mechanism: str = Field(description="как событие действует через этот фактор, 1–2 предложения")
    who_loses: str = ""
    who_wins: str = ""
    horizon: str = ""


class BankAlignment(BaseModel):
    thesis_id: str
    stance: Literal["agrees", "contradicts", "context"] = "context"
    note: str = ""


class EventBrief(BaseModel):
    """Разбор события, общий для всех клиентов: считается один раз и кэшируется (экономия бюджета)."""
    summary: str
    key_facts: list[SourcedText] = Field(default_factory=list)
    forecasts: list[SourcedText] = Field(default_factory=list)
    effective_dates: list[str] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    bank_alignment: list[BankAlignment] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    why: str = ""


class PlanOut(BaseModel):
    focus: str = ""
    steps: list[PlanStep] = Field(default_factory=list)


class ClassificationOut(BaseModel):
    summary: str
    event_type: str = "other"
    factors: list[dict] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    forecasts_or_opinions: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    importance: int = Field(3, ge=1, le=5)
    regions: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    effective_dates: list[str] = Field(default_factory=list)


class ClaimOut(BaseModel):
    text: str
    kind: Literal["fact", "assumption", "bank_view", "ai_inference"] = "ai_inference"
    sources: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


class ActionOut(ClaimOut):
    action_type: Literal["analysis", "hedge", "finance", "operations", "monitor", "question"] = "analysis"
    owner: str = ""
    due: str = ""


class ConsequencesOut(BaseModel):
    risks: list[ClaimOut] = Field(default_factory=list)
    opportunities: list[ClaimOut] = Field(default_factory=list)
    drivers: list[ClaimOut] = Field(default_factory=list)


class ProductOut(BaseModel):
    product: str
    why: str


class InsightDraftOut(BaseModel):
    headline: str
    takeaway: ClaimOut
    what_happened: list[ClaimOut]
    why_it_matters: list[ClaimOut]
    consequences: ConsequencesOut
    # модель иногда пропускает раздел целиком — тогда он берётся из детерминированного черновика,
    # а не весь разбор уходит на правила (замер 21.10: 2 из 12 разборов)
    actions: list[ActionOut] = Field(default_factory=list)
    bank_view: list[ClaimOut] = Field(default_factory=list)
    products: list[ProductOut] = Field(default_factory=list)
    what_could_change: list[ClaimOut] = Field(default_factory=list)
    materiality: int | None = Field(None, ge=0, le=3)
    materiality_why: str = ""


class CriticOut(BaseModel):
    verdict: Literal["ok", "revise"] = "ok"
    issues: list[str] = Field(default_factory=list)
    scores: dict[str, int] = Field(default_factory=dict, description="оценки 1–5 по осям")
