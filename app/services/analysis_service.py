from __future__ import annotations

from uuid import uuid4

from app.core.errors import StockPilotError
from app.domain.analysis import AnalysisRequest, AnalysisView
from app.domain.recommendation import apply_recommendation_policy
from app.providers.llm.base import AnalysisEngine
from app.providers.market_data.base import MarketDataProvider
from app.repositories.analysis_repository import AnalysisRepository


class AnalysisService:
    def __init__(
        self,
        market_data: MarketDataProvider,
        analysis_engine: AnalysisEngine,
        repository: AnalysisRepository,
        allowed_models: tuple[str, ...],
    ) -> None:
        self._market_data = market_data
        self._analysis_engine = analysis_engine
        self._repository = repository
        self._allowed_models = allowed_models

    def run(self, request: AnalysisRequest) -> AnalysisView:
        if request.model not in self._allowed_models:
            allowed = ", ".join(self._allowed_models)
            raise ValueError(f"Model '{request.model}' is not allowed. Choose one of: {allowed}")

        analysis_id = str(uuid4())
        self._repository.create_running(analysis_id, request)

        try:
            snapshot = self._market_data.get_stock_snapshot(request.symbol)
            generated = self._analysis_engine.analyze(snapshot, request)
            result = apply_recommendation_policy(
                generated,
                owns_stock=request.owns_stock,
            )
            self._repository.complete(analysis_id, snapshot, result)
        except StockPilotError as exc:
            self._repository.fail(analysis_id, str(exc))
            raise
        except Exception:
            self._repository.fail(analysis_id, "Unexpected analysis failure")
            raise

        return self._repository.get(analysis_id)

    def get(self, analysis_id: str) -> AnalysisView:
        return self._repository.get(analysis_id)

    def list_recent(self, limit: int = 20) -> list[AnalysisView]:
        return self._repository.list_recent(limit)

