from typing import Protocol

from app.domain.analysis import AnalysisRequest, GeneratedAnalysis
from app.domain.market_data import StockSnapshot


class AnalysisEngine(Protocol):
    def analyze(
        self,
        snapshot: StockSnapshot,
        request: AnalysisRequest,
    ) -> GeneratedAnalysis: ...

