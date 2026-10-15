from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import AnalysisProviderError, InvalidSymbolError, MarketDataError
from app.domain.analysis import (
    AnalysisRequest,
    DecisionFactor,
    EvidenceItem,
    FutureEntryPlan,
    GeneratedAnalysis,
    RecommendationAction,
)
from app.domain.market_data import StockSnapshot
from app.domain.news import NewsAssessment, NewsEvent
from app.providers.market_data.fundamentals import YahooFundamentalsProvider
from app.main import create_app
from tests.unit.test_fundamentals import FakeTicker
from tests.unit.test_news import FakeNewsProvider


class FakeMarketProvider:
    def get_stock_snapshot(self, symbol: str) -> StockSnapshot:
        return StockSnapshot(
            symbol=symbol,
            company_name="Example Corporation",
            exchange="NASDAQ",
            currency="USD",
            as_of=datetime(2026, 10, 5, tzinfo=UTC),
            source="Test fixture",
            current_price=100,
            previous_close=99,
            daily_change_percent=1.0101,
            fifty_two_week_high=110,
            fifty_two_week_low=75,
            sma_20=98,
            sma_50=95,
            sma_200=90,
            rsi_14=58,
            annualized_volatility_percent=22,
            return_1_month_percent=4,
            return_3_month_percent=8,
            return_1_year_percent=12,
            trading_days=252,
        )


class FakeAnalysisEngine:
    def analyze(
        self,
        snapshot: StockSnapshot,
        request: AnalysisRequest,
    ) -> GeneratedAnalysis:
        return GeneratedAnalysis(
            action=RecommendationAction.BUY,
            confidence=0.76,
            summary="The supplied trend measures are constructive, with known limits.",
            bull_case=["Price is above the supplied moving averages."],
            bear_case=["Future growth may not justify the valuation."],
            risks=["Historical price behavior may not continue."],
            evidence=[
                EvidenceItem(
                    claim="Price is above the 200-day average.",
                    metric="sma_200",
                    value="90.00 USD",
                )
            ],
            future_entry_plan=FutureEntryPlan(
                status="A staged entry may be considered",
                conditions=["Price remains above the 200-day average"],
                invalidation_conditions=["Price breaks below the long-term trend"],
            ),
            data_limitations=["No original filings were supplied."],
            fundamental_analysis="Annual revenue grew 20% to USD 1,200.",
            valuation_assessment="Trailing P/E is 20×; intrinsic value remains uncertain.",
            comparison_analysis="Revenue increased from USD 1,000 to USD 1,200.",
            news_analysis=NewsAssessment(
                sentiment="positive", summary="Reported guidance supports growth expectations.",
                recommendation_impact="Guidance supports BUY alongside the financial evidence; monitor delivery against it.",
                events=[NewsEvent(
                    headline="Revenue guidance raised", article_ids=[snapshot.news.articles[0].id],
                    evidence_type="reported", direction="positive",
                    implication="Higher guidance supports the revenue outlook, subject to execution risk.",
                )] if snapshot.news and snapshot.news.articles else [],
            ),
        )


def make_client(tmp_path, fundamentals_provider=None, analysis_engine=None, news_provider=None,
                market_provider=None) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        openai_api_key="test-key",
        openai_default_model="test-model",
        openai_allowed_models="test-model",
    )
    app = create_app(
        settings=settings,
        market_provider=market_provider or FakeMarketProvider(),
        analysis_engine=analysis_engine or FakeAnalysisEngine(),
        fundamentals_provider=fundamentals_provider or YahooFundamentalsProvider(ticker_factory=lambda symbol: FakeTicker()),
        news_provider=news_provider or FakeNewsProvider(),
    )
    return TestClient(app)


def test_health_and_home(tmp_path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.get("/")
        assert response.status_code == 200
        assert "Turn a stock symbol into a clear next step" in response.text


def test_create_and_retrieve_analysis(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "symbol": "test",
                "horizon": "long_term",
                "owns_stock": False,
                "model": "test-model",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["symbol"] == "TEST"
        assert body["action"] == "BUY"
        assert body["market_data"]["source"] == "Test fixture"
        assert body["market_data"]["fundamentals"]["annual_financials"][0]["revenue"] == 1200

        stored = client.get(f"/api/v1/analyses/{body['id']}")
        assert stored.status_code == 200
        assert stored.json()["analysis"]["confidence"] == 0.76

        history = client.get("/api/v1/analyses")
        assert history.status_code == 200
        assert len(history.json()) == 1


def test_html_form_redirects_to_result(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/analyze",
            data={
                "symbol": "AAPL",
                "horizon": "medium_term",
                "position": "not_owned",
                "model": "test-model",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert detail.status_code == 200
        assert "Example Corporation" in detail.text
        assert "BUY" in detail.text
        assert "Company fundamentals" in detail.text
        assert "Is the price justified?" in detail.text
        assert "Annual financial history" in detail.text
        assert "News &amp; catalysts" in detail.text
        assert "https://example.com/guidance" in detail.text
        assert "Revenue guidance raised" in detail.text
        assert "Example Wire" in detail.text


def test_results_page_loads_while_llm_is_still_running(tmp_path) -> None:
    response_sent = Event()
    engine_started = Event()
    release_engine = Event()

    class CountingMarketProvider(FakeMarketProvider):
        calls = 0

        def get_stock_snapshot(self, symbol):
            assert not response_sent.is_set(), "Stock symbol must be checked before redirecting"
            self.calls += 1
            return super().get_stock_snapshot(symbol)

    market_provider = CountingMarketProvider()

    class BlockingEngine(FakeAnalysisEngine):
        def analyze(self, snapshot, request):
            assert response_sent.is_set(), "Redirect must be sent before the LLM runs"
            assert market_provider.calls == 1
            engine_started.set()
            assert release_engine.wait(10), "Test did not release the engine"
            return super().analyze(snapshot, request)

    class ObserveResponse:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            async def observe(message):
                await send(message)
                if (scope.get("path") == "/analyze"
                        and message["type"] == "http.response.body"
                        and not message.get("more_body", False)):
                    response_sent.set()

            await self.app(scope, receive, observe)

    test_client = make_client(tmp_path, analysis_engine=BlockingEngine(),
                              market_provider=market_provider)
    test_client.app.add_middleware(ObserveResponse)
    with test_client as client, ThreadPoolExecutor(max_workers=1) as pool:
        # TestClient itself waits for background tasks, unlike a real browser.
        submitted = pool.submit(client.post, "/analyze", data={
            "symbol": "aapl", "horizon": "medium_term", "position": "not_owned",
            "model": "test-model",
        }, follow_redirects=False)
        try:
            assert engine_started.wait(5)
            assert response_sent.is_set()
            history = client.get("/api/v1/analyses").json()
            assert len(history) == 1
            analysis_id = history[0]["id"]
            assert history[0]["status"] == "running"
            page = client.get(f"/analyses/{analysis_id}")
            assert page.status_code == 200
            assert "Reviewing AAPL" in page.text
            assert f'data-analysis-id="{analysis_id}"' in page.text
            assert "This analysis could not be completed" not in page.text
            assert client.get(f"/api/v1/analyses/{analysis_id}").json()["status"] == "running"
            assert "running" in client.get("/").text
        finally:
            release_engine.set()
        response = submitted.result(timeout=5)
        assert response.status_code == 303
        assert response.headers["location"] == f"/analyses/{analysis_id}"
        assert client.get(f"/api/v1/analyses/{analysis_id}").json()["status"] == "completed"
        page = client.get(response.headers["location"])
        assert "Example Corporation" in page.text
        assert "data-analysis-id" not in page.text
        assert len(client.get("/api/v1/analyses").json()) == 1
        assert market_provider.calls == 1


@pytest.mark.parametrize("failure,status", [
    (InvalidSymbolError("No market data was found for MFST. Check the stock symbol."), 422),
    (MarketDataError("Market data is temporarily unavailable."), 502),
    (MarketDataError("Market data timed out. Please try again."), 502),
])
def test_ticker_check_failure_stays_on_form_without_starting_analysis(tmp_path, failure, status):
    class BrokenMarketProvider:
        def get_stock_snapshot(self, symbol):
            assert symbol == "MFST"
            raise failure

    class UnusedEngine:
        calls = 0

        def analyze(self, snapshot, request):
            self.calls += 1
            raise AssertionError("LLM must not run when stock symbol validation fails")

    engine = UnusedEngine()
    with make_client(tmp_path, market_provider=BrokenMarketProvider(),
                     analysis_engine=engine) as client:
        response = client.post("/analyze", data={
            "symbol": "MFST", "horizon": "long_term", "position": "owned",
            "model": "test-model", "peer_symbols": "AAPL, GOOGL",
        }, follow_redirects=False)
        assert response.status_code == status
        assert "location" not in response.headers
        assert 'id="analysis-form"' in response.text
        assert str(failure) in response.text
        assert 'value="MFST"' in response.text
        assert 'value="long_term" checked' in response.text
        assert 'value="owned" checked' in response.text
        assert 'value="test-model" selected' in response.text
        assert 'value="AAPL, GOOGL"' in response.text
        assert client.get("/api/v1/analyses").json() == []
        assert engine.calls == 0


@pytest.mark.parametrize("failure,message", [
    (AnalysisProviderError("The model timed out. Try again."), "The model timed out. Try again."),
    (RuntimeError("private upstream details"), "Unexpected analysis failure"),
])
def test_background_failure_is_saved_and_displayed(tmp_path, failure, message):
    class BrokenEngine:
        def analyze(self, snapshot, request):
            raise failure

    with make_client(tmp_path, analysis_engine=BrokenEngine()) as client:
        response = client.post("/analyze", data={
            "symbol": "AAPL", "horizon": "medium_term", "position": "not_owned",
            "model": "test-model",
        }, follow_redirects=False)
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert page.status_code == 200
        assert "This analysis could not be completed" in page.text
        assert message in page.text
        assert "private upstream details" not in page.text
        assert "data-analysis-id" not in page.text
        assert client.get("/api/v1/analyses").json()[0]["status"] == "failed"


@pytest.mark.parametrize("overrides", [
    {"symbol": "<script>"}, {"model": "not-allowed"}, {"horizon": "invalid"},
])
def test_invalid_form_does_not_start_a_background_analysis(tmp_path, overrides):
    class UnusedMarketProvider:
        calls = 0

        def get_stock_snapshot(self, symbol):
            self.calls += 1
            raise AssertionError("Invalid form must not call the provider")

    market_provider = UnusedMarketProvider()
    with make_client(tmp_path, market_provider=market_provider) as client:
        data = {"symbol": "AAPL", "horizon": "medium_term", "position": "not_owned",
                "model": "test-model"} | overrides
        response = client.post("/analyze", data=data, follow_redirects=False)
        assert response.status_code == 422
        assert client.get("/api/v1/analyses").json() == []
        assert market_provider.calls == 0


def test_owned_position_does_not_display_watch(tmp_path) -> None:
    class WatchEngine(FakeAnalysisEngine):
        def analyze(self, snapshot, request):
            result = super().analyze(snapshot, request)
            result.action = RecommendationAction.WATCH
            result.summary = "Watch: valuation is uncertain."
            return result

    with make_client(tmp_path, analysis_engine=WatchEngine()) as client:
        data = client.post("/api/v1/analyses", json={
            "symbol": "AAPL", "horizon": "medium_term", "owns_stock": True,
            "model": "test-model",
        }).json()
        assert data["action"] == "HOLD"
        assert data["analysis"]["summary"].startswith("Hold:")
        page = client.get(f"/analyses/{data['id']}").text
        assert "Current position" in page
        assert "Hold: maintain the existing position" in page


def test_disallowed_model_is_rejected(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "symbol": "AAPL",
                "horizon": "medium_term",
                "owns_stock": False,
                "model": "not-allowed",
            },
        )
        assert response.status_code == 422


def test_peer_input_reaches_model_persistence_and_html(tmp_path):
    class InspectingEngine(FakeAnalysisEngine):
        def analyze(self, snapshot, request):
            assert [peer.symbol for peer in snapshot.peer_fundamentals] == ["MSFT", "GOOGL"]
            assert snapshot.fundamentals.annual_financials[0].revenue == 1200
            return super().analyze(snapshot, request)

    with make_client(tmp_path, analysis_engine=InspectingEngine()) as client:
        response = client.post("/analyze", data={
            "symbol": "AAPL", "horizon": "long_term", "position": "not_owned",
            "model": "test-model", "peer_symbols": "msft, GOOGL, AAPL",
        }, follow_redirects=False)
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert detail.status_code == 200
        assert "MSFT" in detail.text and "GOOGL" in detail.text
        assert "Trailing P/E is 20" in detail.text
        stored = client.get("/api/v1/analyses").json()[0]
        assert len(stored["market_data"]["peer_fundamentals"]) == 2


def test_fundamentals_outage_preserves_analysis_and_blocks_long_term_buy(tmp_path):
    class BrokenProvider:
        def get_fundamentals(self, symbol):
            raise TimeoutError("Provider unavailable")

    with make_client(tmp_path, fundamentals_provider=BrokenProvider()) as client:
        response = client.post("/api/v1/analyses", json={
            "symbol": "AAPL", "horizon": "long_term", "owns_stock": False, "model": "test-model",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "WATCH"
        assert data["confidence"] <= .4
        assert data["market_data"]["fundamentals"]["status"] == "unavailable"
        assert data["market_data"]["current_price"] == 100
        assert "Wait for financial" in data["analysis"]["future_entry_plan"]["status"]
        assert "inconclusive" in data["analysis"]["valuation_assessment"]
        assert "20×" not in data["analysis"]["valuation_assessment"]
        assert client.get(f"/analyses/{data['id']}").status_code == 200


def test_peer_limit_and_invalid_symbols_are_rejected(tmp_path):
    with make_client(tmp_path) as client:
        for peers in (["A", "B", "C", "D"], ["<script>"]):
            response = client.post("/api/v1/analyses", json={
                "symbol": "AAPL", "model": "test-model", "peer_symbols": peers,
            })
            assert response.status_code == 422


def test_news_reaches_model_and_is_saved_with_source_linked_impact(tmp_path):
    class InspectingEngine(FakeAnalysisEngine):
        def analyze(self, snapshot, request):
            assert snapshot.news.symbol == "AAPL"
            assert snapshot.news.articles[0].summary == "Management raised its full-year revenue guidance."
            assert snapshot.news.status == "available"
            return super().analyze(snapshot, request)

    with make_client(tmp_path, analysis_engine=InspectingEngine()) as client:
        response = client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"})
        assert response.status_code == 201
        data = response.json()
        stored = client.get(f"/api/v1/analyses/{data['id']}").json()
        assert stored["market_data"]["news"] == data["market_data"]["news"]
        assert stored["analysis"]["news_analysis"]["events"][0]["article_ids"] == [
            stored["market_data"]["news"]["articles"][0]["id"]
        ]
        assert "supports BUY" in stored["analysis"]["news_analysis"]["recommendation_impact"]


def test_news_outage_preserves_report_and_guards_short_term_buy(tmp_path):
    class BrokenProvider:
        def get_news(self, symbol):
            raise TimeoutError("upstream credentials must not leak")

    with make_client(tmp_path, news_provider=BrokenProvider()) as client:
        response = client.post("/api/v1/analyses", json={
            "symbol": "AAPL", "model": "test-model", "horizon": "short_term",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "WATCH"
        assert data["confidence"] == .55
        assert data["market_data"]["news"]["status"] == "unavailable"
        assert data["market_data"]["current_price"] == 100
        assert data["analysis"]["news_analysis"]["sentiment"] == "unknown"
        assert data["analysis"]["news_analysis"]["events"] == []
        assert "WATCH" in data["analysis"]["news_analysis"]["recommendation_impact"]
        assert "upstream credentials" not in response.text
        page = client.get(f"/analyses/{data['id']}")
        assert page.status_code == 200
        assert "Coverage: Unavailable" in page.text


def test_old_saved_report_renders_without_news(tmp_path):
    import json
    import sqlite3

    with make_client(tmp_path) as client:
        data = client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"}).json()
        market = data["market_data"]
        result = data["analysis"]
        market.pop("news")
        result.pop("news_analysis")
        with sqlite3.connect(tmp_path / "test.db") as db:
            db.execute("UPDATE analyses SET market_data_json = ?, result_json = ? WHERE id = ?",
                       (json.dumps(market), json.dumps(result), data["id"]))
        page = client.get(f"/analyses/{data['id']}")
        assert page.status_code == 200
        assert "News was not assessed in this saved analysis" in page.text


def test_untrusted_news_text_is_escaped_in_html(tmp_path):
    class UnsafeTextProvider(FakeNewsProvider):
        def get_news(self, symbol):
            news = super().get_news(symbol)
            news.articles[0].title = '<script>alert("injected")</script>'
            news.articles[0].summary = '<img src=x onerror="alert(1)">'
            return news

    with make_client(tmp_path, news_provider=UnsafeTextProvider()) as client:
        data = client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"}).json()
        page = client.get(f"/analyses/{data['id']}")
        assert "<script>alert" not in page.text
        assert "&lt;script&gt;alert" in page.text
        assert "<img src=x" not in page.text


def test_view_orders_cross_case_factors_by_decision_rank(tmp_path):
    class RankedEngine(FakeAnalysisEngine):
        def analyze(self, snapshot, request):
            result = super().analyze(snapshot, request)
            result.summary = "Buy: the current valuation is the decisive reason."
            result.decision_factors = [
                DecisionFactor(rank=3, role="support", point="Trend adds context."),
                DecisionFactor(rank=1, role="support", point="Valuation supports an entry."),
                DecisionFactor(rank=2, role="risk", point="Debt limits conviction."),
            ]
            return result

    with make_client(tmp_path, analysis_engine=RankedEngine()) as client:
        data = client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"}).json()
        page = client.get(f"/analyses/{data['id']}").text
        assert page.index("Buy: the current valuation") < page.index("Valuation supports an entry")
        assert page.index("Valuation supports an entry") < page.index("Debt limits conviction")
        assert page.index("Debt limits conviction") < page.index("Trend adds context")
        assert "Full bull and bear cases" in page
        assert data["analysis"]["decision_factors"][0]["rank"] == 3  # Raw model order is preserved in JSON.
