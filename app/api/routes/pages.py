from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.dependencies import AppContainer, get_container
from app.core.errors import AnalysisNotFoundError, StockPilotError
from app.domain.analysis import AnalysisRequest, InvestmentHorizon

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def _page_context(request: Request, container: AppContainer) -> dict[str, object]:
    return {
        "request": request,
        "models": container.settings.allowed_models,
        "default_model": container.settings.openai_default_model,
        "history": container.analysis_service.list_recent(12),
    }


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_page_context(request, container),
    )


@router.post("/analyze", response_class=HTMLResponse)
def analyze(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
    symbol: Annotated[str, Form()],
    horizon: Annotated[str, Form()],
    position: Annotated[str, Form()],
    model: Annotated[str, Form()],
) -> HTMLResponse:
    context = _page_context(request, container)
    context["form"] = {
        "symbol": symbol,
        "horizon": horizon,
        "position": position,
        "model": model,
    }

    try:
        analysis_request = AnalysisRequest(
            symbol=symbol,
            horizon=InvestmentHorizon(horizon),
            owns_stock=position == "owned",
            model=model,
        )
        result = container.analysis_service.run(analysis_request)
    except (ValidationError, ValueError) as exc:
        context["error"] = _validation_message(exc)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context=context,
            status_code=422,
        )
    except StockPilotError as exc:
        context["error"] = str(exc)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context=context,
            status_code=502,
        )

    return RedirectResponse(url=f"/analyses/{result.id}", status_code=303)


@router.get("/analyses/{analysis_id}", response_class=HTMLResponse)
def analysis_detail(
    analysis_id: str,
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
) -> HTMLResponse:
    try:
        analysis = container.analysis_service.get(analysis_id)
    except AnalysisNotFoundError as exc:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"request": request, "message": str(exc)},
            status_code=404,
        )
    return templates.TemplateResponse(
        request=request,
        name="analysis_detail.html",
        context={"request": request, "analysis": analysis},
    )


def _validation_message(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            return str(errors[0].get("msg", "Check the submitted values."))
    return str(exc)

