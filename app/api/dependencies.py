from dataclasses import dataclass

from fastapi import Request

from app.core.config import Settings
from app.services.analysis_service import AnalysisService
from app.services.auth_service import AuthService


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    analysis_service: AnalysisService
    auth_service: AuthService


def get_container(request: Request) -> AppContainer:
    return request.app.state.container

