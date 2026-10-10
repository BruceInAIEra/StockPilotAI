from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.config import Settings
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


def make_client(tmp_path, fundamentals_provider=None, analysis_engine=None, news_provider=None) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        openai_api_key="test-key",
        openai_default_model="test-model",
        openai_allowed_models="test-model",
    )
    app = create_app(
        settings=settings,
        market_provider=FakeMarketProvider(),
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
        assert "Turn a ticker into a clear next step" in response.text


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
