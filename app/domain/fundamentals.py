from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FundamentalMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    key: str
    label: str
    value: float
    unit: str
    period: str


class AnnualFinancials(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    period_end: date
    revenue: float | None = None
    revenue_growth_percent: float | None = None
    net_income: float | None = None
    operating_margin_percent: float | None = None
    free_cash_flow: float | None = None


class FundamentalsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    status: Literal["available", "partial", "unavailable"] = "unavailable"
    retrieved_at: datetime
    source: str = "Yahoo Finance via yfinance"
    source_url: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    financial_currency: str | None = None
    latest_statement_date: date | None = None
    metrics: list[FundamentalMetric] = Field(default_factory=list)
    annual_financials: list[AnnualFinancials] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
