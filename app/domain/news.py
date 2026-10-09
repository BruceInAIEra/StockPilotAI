from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator


class NewsArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    publisher: str
    url: str
    published_at: AwareDatetime
    summary: str | None = None

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        return str(TypeAdapter(HttpUrl).validate_python(value))


class NewsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    status: Literal["available", "partial", "empty", "stale", "unavailable"]
    retrieved_at: datetime
    window_start: datetime
    source: str = "Yahoo Finance via yfinance"
    articles: list[NewsArticle] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class NewsEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str
    article_ids: list[str] = Field(min_length=1, description="IDs of supplied articles supporting this event")
    evidence_type: Literal["reported", "rumor", "opinion"]
    direction: Literal["positive", "negative", "mixed", "uncertain"]
    implication: str = Field(description="Explain the business or valuation mechanism, horizon, and uncertainty")


class NewsAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentiment: Literal["positive", "negative", "mixed", "neutral", "unknown"]
    summary: str
    recommendation_impact: str = Field(description="Explain how news changes or supports the final action, confidence, and entry/invalidation conditions")
    events: list[NewsEvent]
