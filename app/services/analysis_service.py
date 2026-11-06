from __future__ import annotations

import logging
from uuid import uuid4

from app.core.errors import StockPilotError
from app.domain.analysis import AnalysisRequest, AnalysisView
from app.domain.fundamentals import FundamentalsSnapshot
from app.domain.market_data import StockSnapshot
from app.domain.recommendation import apply_recommendation_policy
from app.providers.llm.base import AnalysisEngine
from app.providers.market_data.base import MarketDataProvider
from app.providers.market_data.fundamentals import FundamentalsProvider, unavailable_fundamentals
from app.providers.market_data.news import NewsProvider, unavailable_news
from app.repositories.analysis_repository import AnalysisRepository

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(
        self,
        market_data: MarketDataProvider,
        analysis_engine: AnalysisEngine,
        repository: AnalysisRepository,
        allowed_models: tuple[str, ...],
        fundamentals_provider: FundamentalsProvider | None = None,
        news_provider: NewsProvider | None = None,
        model_settings_provider=None,
    ) -> None:
        self._market_data = market_data
        self._analysis_engine = analysis_engine
        self._repository = repository
        self._allowed_models = allowed_models
        self._model_settings_provider = model_settings_provider
        self._fundamentals_provider = fundamentals_provider
        self._news_provider = news_provider

    def run(self, request: AnalysisRequest, user_id: str) -> AnalysisView:
        analysis = self.create(request, user_id)
        return self._execute(analysis.id, request)

    def create(self, request: AnalysisRequest, user_id: str) -> AnalysisView:
        """Validate and persist the job before any slow provider calls."""
        self._validate_model(request)
        analysis_id = str(uuid4())
        self._repository.create_running(analysis_id, request, user_id)
        return self._repository.get(analysis_id, user_id)

    def prepare(self, request: AnalysisRequest, user_id: str) -> tuple[AnalysisView, StockSnapshot]:
        """Verify stock symbol availability before creating a background analysis."""
        self._validate_model(request)
        snapshot = self._market_data.get_stock_snapshot(request.symbol)
        return self.create(request, user_id), snapshot

    def _validate_model(self, request: AnalysisRequest) -> None:
        models = (self._model_settings_provider().models if self._model_settings_provider
                  else self._allowed_models)
        if request.model not in models:
            allowed = ", ".join(models)
            raise ValueError(f"Model '{request.model}' is not allowed. Choose one of: {allowed}")

    def run_background(
        self, analysis_id: str, request: AnalysisRequest, snapshot: StockSnapshot | None = None
    ) -> None:
        try:
            self._execute(analysis_id, request, snapshot)
        except Exception:
            # _execute saves the failure; the initial HTTP response is already sent.
            logger.error("Background analysis failed: id=%s symbol=%s", analysis_id, request.symbol)

    def _execute(
        self, analysis_id: str, request: AnalysisRequest, snapshot: StockSnapshot | None = None
    ) -> AnalysisView:
        try:
            if snapshot is None:
                snapshot = self._market_data.get_stock_snapshot(request.symbol)
            if self._fundamentals_provider:
                snapshot.fundamentals = self._get_fundamentals(request.symbol)
                snapshot.peer_fundamentals = [
                    self._get_fundamentals(symbol)
                    for symbol in request.peer_symbols if symbol != request.symbol
                ]
            try:
                snapshot.news = (self._news_provider.get_news(request.symbol)
                                 if self._news_provider else unavailable_news(request.symbol))
            except Exception:
                snapshot.news = unavailable_news(request.symbol)
            generated = self._analysis_engine.analyze(snapshot, request)
            result = apply_recommendation_policy(
                generated,
                owns_stock=request.owns_stock,
                snapshot=snapshot,
                horizon=request.horizon,
            )
            self._repository.complete(analysis_id, snapshot, result)
        except StockPilotError as exc:
            self._repository.fail(analysis_id, str(exc))
            raise
        except Exception:
            self._repository.fail(analysis_id, "Unexpected analysis failure")
            raise

        return self._repository.get_for_worker(analysis_id)

    def _get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        try:
            if self._fundamentals_provider is None:
                return unavailable_fundamentals(symbol)
            return self._fundamentals_provider.get_fundamentals(symbol)
        except Exception:
            return unavailable_fundamentals(symbol)

    def get(self, analysis_id: str, user_id: str) -> AnalysisView:
        return self._repository.get(analysis_id, user_id)

    def list_recent(self, user_id: str, limit: int = 20, offset: int = 0) -> list[AnalysisView]:
        return self._repository.list_recent(user_id, limit, offset)
