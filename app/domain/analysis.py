from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.news import NewsAssessment


class InvestmentHorizon(str, Enum):
    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


class RecommendationAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    WATCH = "WATCH"


class AnalysisStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=15)
    horizon: InvestmentHorizon = InvestmentHorizon.MEDIUM_TERM
    owns_stock: bool = False
    model: str = Field(min_length=1, max_length=100)
    peer_symbols: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("peer_symbols")
    @classmethod
    def normalize_peers(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(cls.normalize_symbol(value) for value in values))

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9.\-^=]{1,15}", symbol):
            raise ValueError("Enter a valid ticker such as AAPL, BRK.B, or BTC-USD")
        return symbol

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Select a model")
        return value


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(description="A concise factual claim grounded in the supplied snapshot")
    metric: str = Field(description="The exact metric name supporting the claim")
    value: str = Field(description="The supplied metric value, including units when relevant")


class FutureEntryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="Whether to buy now, hold, exit, or wait for conditions")
    conditions: list[str] = Field(description="Observable conditions that could support a future entry")
    invalidation_conditions: list[str] = Field(
        description="Observable conditions that would weaken or invalidate the thesis"
    )


class GeneratedAnalysis(BaseModel):
    """Strict model-generated portion of an analysis."""

    model_config = ConfigDict(extra="forbid")

    action: RecommendationAction
    confidence: float = Field(ge=0, le=1)
    summary: str
    bull_case: list[str]
    bear_case: list[str]
    risks: list[str]
    evidence: list[EvidenceItem]
    future_entry_plan: FutureEntryPlan
    data_limitations: list[str]
    fundamental_analysis: str = Field(
        default="Not assessed in this saved analysis.",
        description="Assess revenue growth, margins, earnings, cash flow, and debt using supplied data.",
    )
    valuation_assessment: str = Field(
        default="Not assessed in this saved analysis.",
        description="Explain whether valuation appears demanding, reasonable, or inconclusive; cite evidence and assumptions.",
    )
    comparison_analysis: str = Field(
        default="Not assessed in this saved analysis.",
        description="Compare annual revenue and supplied peers, explaining fiscal period and business differences.",
    )
    news_analysis: NewsAssessment | None = Field(
        default=None,
        description="Assess supplied news, cite article IDs, and explain its impact on the final recommendation. Always complete for new analyses.",
    )


class AnalysisView(BaseModel):
    id: str
    created_at: datetime
    symbol: str
    company_name: str
    horizon: InvestmentHorizon
    owns_stock: bool
    model: str
    status: AnalysisStatus
    action: RecommendationAction | None = None
    confidence: float | None = None
    analysis: GeneratedAnalysis | None = None
    market_data: "StockSnapshot | None" = None
    error_message: str | None = None


from app.domain.market_data import StockSnapshot  # noqa: E402

AnalysisView.model_rebuild()
