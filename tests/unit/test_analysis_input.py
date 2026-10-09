import json

from app.domain.analysis import AnalysisRequest
from app.domain.market_data import StockSnapshot
from app.providers.llm.prompt import build_analysis_input
from tests.integration.test_app import FakeMarketProvider
from tests.unit.test_fundamentals import snapshot
from tests.unit.test_news import news_snapshot


def test_prompt_contains_financial_evidence_and_peer_data():
    market = FakeMarketProvider().get_stock_snapshot("TEST")
    market.fundamentals = snapshot()
    market.peer_fundamentals = [snapshot().model_copy(update={"symbol": "PEER"})]
    market.news = news_snapshot()
    request = AnalysisRequest(symbol="TEST", model="test", peer_symbols=["PEER"])
    payload = json.loads(build_analysis_input(market, request).split("\n\n", 1)[1])
    supplied = payload["market_snapshot"]
    assert supplied["fundamentals"]["annual_financials"][0]["revenue"] == 1200
    assert supplied["fundamentals"]["financial_currency"] == "USD"
    assert supplied["peer_fundamentals"][0]["symbol"] == "PEER"
    assert supplied["news"]["articles"][0]["publisher"] == "Example Wire"
    assert supplied["news"]["articles"][0]["published_at"] == "2026-10-08T12:00:00Z"
    assert supplied["news"]["articles"][0]["summary"] == "Management raised its full-year revenue guidance."


def test_price_only_saved_snapshot_remains_readable():
    saved = FakeMarketProvider().get_stock_snapshot("TEST").model_dump()
    saved.pop("fundamentals")
    saved.pop("peer_fundamentals")
    saved.pop("news")
    restored = StockSnapshot.model_validate(saved)
    assert restored.fundamentals is None
    assert restored.peer_fundamentals == []
    assert restored.news is None
