from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.core.errors import InvalidSymbolError, MarketDataError
from app.domain.market_data import StockSnapshot
from app.services.indicators import (
    annualized_volatility,
    percent_return,
    relative_strength_index,
    simple_moving_average,
)


class YahooFinanceProvider:
    """No-key MVP adapter for Yahoo's public chart response.

    The endpoint is not a contractual market-data feed, so callers depend only on
    MarketDataProvider and can replace this adapter for production use.
    """

    base_url = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, timeout_seconds: float = 20) -> None:
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": "StockPilotAI/0.1 local-research-app"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def get_stock_snapshot(self, symbol: str) -> StockSnapshot:
        try:
            response = self._client.get(
                self.base_url.format(symbol=symbol),
                params={
                    "range": "1y",
                    "interval": "1d",
                    "events": "div,splits",
                    "includeAdjustedClose": "true",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise MarketDataError("Market data timed out. Please try again.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MarketDataError("Market data is temporarily unavailable.") from exc

        chart = payload.get("chart", {})
        if chart.get("error"):
            description = chart["error"].get("description", "Unknown market-data error")
            if "not found" in description.lower() or "delisted" in description.lower():
                raise InvalidSymbolError(f"No market data was found for {symbol}.")
            raise MarketDataError(description)

        results = chart.get("result") or []
        if not results:
            raise InvalidSymbolError(f"No market data was found for {symbol}.")

        result = results[0]
        meta = result.get("meta", {})
        indicators = result.get("indicators", {})
        quote = (indicators.get("quote") or [{}])[0]
        adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
        raw_closes = quote.get("close") or []
        close_series = adjusted if any(value is not None for value in adjusted) else raw_closes
        closes = [float(value) for value in close_series if value is not None and value > 0]

        if not closes:
            raise InvalidSymbolError(f"No usable price history was found for {symbol}.")

        current_price = float(meta.get("regularMarketPrice") or closes[-1])
        previous_close_value = meta.get("chartPreviousClose") or meta.get("previousClose")
        previous_close = float(previous_close_value) if previous_close_value else None
        daily_change = None
        if previous_close and previous_close != 0:
            daily_change = round(((current_price / previous_close) - 1) * 100, 4)

        market_time = meta.get("regularMarketTime")
        as_of = (
            datetime.fromtimestamp(market_time, tz=UTC)
            if market_time
            else datetime.now(tz=UTC)
        )

        return StockSnapshot(
            symbol=symbol,
            company_name=meta.get("longName") or meta.get("shortName") or symbol,
            exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "Unknown",
            currency=meta.get("currency") or "USD",
            as_of=as_of,
            source="Yahoo Finance chart data",
            current_price=round(current_price, 4),
            previous_close=round(previous_close, 4) if previous_close else None,
            daily_change_percent=daily_change,
            fifty_two_week_high=round(max(closes), 4),
            fifty_two_week_low=round(min(closes), 4),
            sma_20=simple_moving_average(closes, 20),
            sma_50=simple_moving_average(closes, 50),
            sma_200=simple_moving_average(closes, 200),
            rsi_14=relative_strength_index(closes, 14),
            annualized_volatility_percent=annualized_volatility(closes),
            return_1_month_percent=percent_return(closes, 21),
            return_3_month_percent=percent_return(closes, 63),
            return_1_year_percent=percent_return(closes, min(251, len(closes) - 1)),
            trading_days=len(closes),
        )

