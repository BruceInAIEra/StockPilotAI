from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.dependencies import AppContainer, get_container
from app.core.errors import AnalysisNotFoundError, InvalidSymbolError, StockPilotError
from app.domain.analysis import AnalysisRequest, InvestmentHorizon

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def financial_number(value: float | None) -> str:
    if value is None:
        return "—"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= threshold:
            return f"{value / threshold:,.2f}{suffix}"
    return f"{value:,.2f}"


templates.env.filters["financial_number"] = financial_number


def _page_context(request: Request, container: AppContainer) -> dict[str, object]:
    current = container.admin_service.model_settings()
    return {
        "request": request,
        "models": current.models,
        "default_model": current.default,
        "history": container.analysis_service.list_recent(request.state.user.id, 12),
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
    background_tasks: BackgroundTasks,
    container: Annotated[AppContainer, Depends(get_container)],
    symbol: Annotated[str, Form()],
    horizon: Annotated[str, Form()],
    position: Annotated[str, Form()],
    model: Annotated[str, Form()],
    peer_symbols: Annotated[str, Form()] = "",
) -> HTMLResponse:
    context = _page_context(request, container)
    context["form"] = {
        "symbol": symbol,
        "horizon": horizon,
        "position": position,
        "model": model,
        "peer_symbols": peer_symbols,
    }

    try:
        analysis_request = AnalysisRequest(
            symbol=symbol,
            horizon=InvestmentHorizon(horizon),
            owns_stock=position == "owned",
            model=model,
            peer_symbols=[value.strip() for value in peer_symbols.split(",") if value.strip()],
        )
        result, snapshot = container.analysis_service.prepare(analysis_request, request.state.user.id)
    except (ValidationError, ValueError, InvalidSymbolError) as exc:
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

    background_tasks.add_task(
        container.analysis_service.run_background, result.id, analysis_request, snapshot
    )
    return RedirectResponse(url=f"/analyses/{result.id}", status_code=303)


@router.get("/analyses/{analysis_id}", response_class=HTMLResponse)
def analysis_detail(
    analysis_id: str,
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
) -> HTMLResponse:
    try:
        analysis = container.analysis_service.get(analysis_id, request.state.user.id)
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


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
    page: Annotated[int, Query(ge=1)] = 1,
):
    rows = container.analysis_service.list_recent(request.state.user.id, 21, (page - 1) * 20)
    return templates.TemplateResponse(request=request, name="history.html", context={
        "history": rows[:20], "page": page, "has_next": len(rows) > 20,
    })


def _validation_message(exc: ValidationError | ValueError | InvalidSymbolError) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            return str(errors[0].get("msg", "Check the submitted values."))
    return str(exc)
