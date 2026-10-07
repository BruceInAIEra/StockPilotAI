from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.errors import AnalysisNotFoundError
from app.domain.analysis import (
    AnalysisRequest,
    AnalysisStatus,
    AnalysisView,
    GeneratedAnalysis,
    InvestmentHorizon,
    RecommendationAction,
)
from app.domain.market_data import StockSnapshot
from app.repositories.database import AnalysisRecord


class AnalysisRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def create_running(self, analysis_id: str, request: AnalysisRequest) -> None:
        with self._session_factory() as session:
            session.add(
                AnalysisRecord(
                    id=analysis_id,
                    symbol=request.symbol,
                    company_name=request.symbol,
                    horizon=request.horizon.value,
                    owns_stock=request.owns_stock,
                    model_name=request.model,
                    status=AnalysisStatus.RUNNING.value,
                    request_json=request.model_dump(mode="json"),
                )
            )
            session.commit()

    def complete(
        self,
        analysis_id: str,
        snapshot: StockSnapshot,
        result: GeneratedAnalysis,
    ) -> None:
        with self._session_factory() as session:
            record = session.get(AnalysisRecord, analysis_id)
            if record is None:
                raise AnalysisNotFoundError(f"Analysis {analysis_id} was not found.")
            record.company_name = snapshot.company_name
            record.market_data_json = snapshot.model_dump(mode="json")
            record.result_json = result.model_dump(mode="json")
            record.action = result.action.value
            record.confidence = result.confidence
            record.status = AnalysisStatus.COMPLETED.value
            session.commit()

    def fail(self, analysis_id: str, message: str) -> None:
        with self._session_factory() as session:
            record = session.get(AnalysisRecord, analysis_id)
            if record is None:
                return
            record.status = AnalysisStatus.FAILED.value
            record.error_message = message
            session.commit()

    def get(self, analysis_id: str) -> AnalysisView:
        with self._session_factory() as session:
            record = session.get(AnalysisRecord, analysis_id)
            if record is None:
                raise AnalysisNotFoundError(f"Analysis {analysis_id} was not found.")
            return self._to_view(record)

    def list_recent(self, limit: int = 20) -> list[AnalysisView]:
        with self._session_factory() as session:
            records = session.scalars(
                select(AnalysisRecord)
                .order_by(AnalysisRecord.created_at.desc())
                .limit(min(max(limit, 1), 100))
            ).all()
            return [self._to_view(record) for record in records]

    @staticmethod
    def _to_view(record: AnalysisRecord) -> AnalysisView:
        return AnalysisView(
            id=record.id,
            created_at=record.created_at,
            symbol=record.symbol,
            company_name=record.company_name,
            horizon=InvestmentHorizon(record.horizon),
            owns_stock=record.owns_stock,
            model=record.model_name,
            status=AnalysisStatus(record.status),
            action=RecommendationAction(record.action) if record.action else None,
            confidence=record.confidence,
            analysis=(
                GeneratedAnalysis.model_validate(record.result_json)
                if record.result_json
                else None
            ),
            market_data=(
                StockSnapshot.model_validate(record.market_data_json)
                if record.market_data_json
                else None
            ),
            error_message=record.error_message,
        )

