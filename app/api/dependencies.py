from dataclasses import dataclass

from fastapi import Request

from app.core.config import Settings
from app.services.analysis_service import AnalysisService


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    analysis_service: AnalysisService


def get_container(request: Request) -> AppContainer:
    return request.app.state.container

