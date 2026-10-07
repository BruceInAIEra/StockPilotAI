from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.analysis import (
    AnalysisRequest,
    EvidenceItem,
    FutureEntryPlan,
    GeneratedAnalysis,
    RecommendationAction,
)
from app.domain.market_data import StockSnapshot
from app.main import create_app


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
            bear_case=["The snapshot excludes fundamentals."],
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
            data_limitations=["No company fundamentals or news were supplied."],
        )


def make_client(tmp_path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        openai_api_key="test-key",
        openai_default_model="test-model",
        openai_allowed_models="test-model",
    )
    app = create_app(
        settings=settings,
        market_provider=FakeMarketProvider(),
        analysis_engine=FakeAnalysisEngine(),
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

