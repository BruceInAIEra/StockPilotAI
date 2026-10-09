from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.fundamentals import FundamentalsSnapshot


class StockSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    company_name: str
    exchange: str
    currency: str
    as_of: datetime
    source: str

    current_price: float
    previous_close: float | None = None
    daily_change_percent: float | None = None
    fifty_two_week_high: float
    fifty_two_week_low: float

    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    rsi_14: float | None = Field(default=None, ge=0, le=100)
    annualized_volatility_percent: float | None = Field(default=None, ge=0)

    return_1_month_percent: float | None = None
    return_3_month_percent: float | None = None
    return_1_year_percent: float | None = None
    trading_days: int = Field(ge=1)
    fundamentals: FundamentalsSnapshot | None = None
    peer_fundamentals: list[FundamentalsSnapshot] = Field(default_factory=list)
