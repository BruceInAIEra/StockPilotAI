from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.core.errors import InvalidSymbolError, MarketDataError
from app.providers.market_data.yahoo import YahooFinanceProvider


def make_provider(payload: dict) -> YahooFinanceProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    provider = YahooFinanceProvider()
    provider._client.close()
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def chart_payload(meta: dict, closes: list[float]) -> dict:
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "longName": "Constellation Energy Corporation",
                        "fullExchangeName": "NasdaqGS",
                        "regularMarketTime": int(
                            datetime(2026, 10, 6, 20, 0, tzinfo=UTC).timestamp()
                        ),
                        **meta,
                    },
                    "indicators": {
                        "quote": [{"close": closes}],
                        "adjclose": [{"adjclose": closes}],
                    },
                }
            ],
        }
    }


def test_daily_change_uses_regular_market_metadata_not_chart_range_anchor() -> None:
    provider = make_provider(
        chart_payload(
            {
                "regularMarketPrice": 300.40,
                "regularMarketChangePercent": 12.249,
                "chartPreviousClose": 364.10,
            },
            [364.10, 257.49, 267.62, 300.40],
        )
    )

    try:
        snapshot = provider.get_stock_snapshot("CEG")
    finally:
        provider.close()

    assert snapshot.daily_change_percent == 12.249
    assert snapshot.previous_close == pytest.approx(267.6193, abs=0.0001)


def test_daily_change_falls_back_to_the_previous_daily_bar() -> None:
    provider = make_provider(
        chart_payload(
            {
                "regularMarketPrice": 300.40,
                "chartPreviousClose": 364.10,
            },
            [364.10, 257.49, 267.62, 300.40],
        )
    )

    try:
        snapshot = provider.get_stock_snapshot("CEG")
    finally:
        provider.close()

    assert snapshot.previous_close == 267.62
    assert snapshot.daily_change_percent == pytest.approx(12.2487, abs=0.0001)


def test_unknown_ticker_404_is_not_reported_as_a_provider_outage() -> None:
    def handler(request):
        return httpx.Response(404, json={
            "chart": {"result": None, "error": {
                "code": "Not Found", "description": "No data found, symbol may be delisted",
            }},
        })

    provider = YahooFinanceProvider()
    provider._client.close()
    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(InvalidSymbolError, match="MFST.*Check the stock symbol"):
            provider.get_stock_snapshot("MFST")
    finally:
        provider.close()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_provider_http_failure_remains_a_market_data_error(status) -> None:
    provider = YahooFinanceProvider()
    provider._client.close()
    provider._client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text="Upstream failure")
    ))
    try:
        with pytest.raises(MarketDataError, match="temporarily unavailable"):
            provider.get_stock_snapshot("MSFT")
    finally:
        provider.close()
