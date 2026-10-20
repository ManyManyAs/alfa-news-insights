from __future__ import annotations

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    id: str
    source: str
    source_title: str | None = None
    url: str | None = None
    title: str
    text: str = ""
    published_at: str | None = None


class FactorHit(BaseModel):
    factor: str
    direction: str = "unknown"  # up | down | stable | unknown
    confidence: float = 0.5
    evidence: str | None = None  # фраза из новости


class EventClassification(BaseModel):
    summary: str
    event_type: str = "other"
    factors: list[FactorHit] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    forecasts_or_opinions: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    importance: int = 3  # 1..5
    security_flags: list[str] = Field(default_factory=list)
    classifier: str = "rules"  # rules | llm:<model>


class Event(BaseModel):
    id: str
    title: str
    published_at: str | None = None
    news: list[NewsItem]
    classification: EventClassification

    @property
    def sources(self) -> list[dict]:
        return [{"news_id": n.id, "title": n.title, "source": n.source_title or n.source, "url": n.url,
                 "published_at": n.published_at} for n in self.news]
