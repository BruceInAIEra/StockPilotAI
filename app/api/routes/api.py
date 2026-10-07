from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import AppContainer, get_container
from app.core.errors import AnalysisNotFoundError, StockPilotError
from app.domain.analysis import AnalysisRequest, AnalysisView

router = APIRouter(prefix="/api/v1", tags=["api"])


@router.get("/models")
def models(
    container: Annotated[AppContainer, Depends(get_container)],
) -> dict[str, object]:
    return {
        "default": container.settings.openai_default_model,
        "models": list(container.settings.allowed_models),
    }


@router.post(
    "/analyses",
    response_model=AnalysisView,
    status_code=status.HTTP_201_CREATED,
)
def create_analysis(
    request: AnalysisRequest,
    container: Annotated[AppContainer, Depends(get_container)],
) -> AnalysisView:
    try:
        return container.analysis_service.run(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StockPilotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/analyses", response_model=list[AnalysisView])
def list_analyses(
    container: Annotated[AppContainer, Depends(get_container)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AnalysisView]:
    return container.analysis_service.list_recent(limit)


@router.get("/analyses/{analysis_id}", response_model=AnalysisView)
def get_analysis(
    analysis_id: str,
    container: Annotated[AppContainer, Depends(get_container)],
) -> AnalysisView:
    try:
        return container.analysis_service.get(analysis_id)
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

