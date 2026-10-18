from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.repositories.database import AnalysisRecord

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    try:
        with request.app.state.session_factory() as session:
            # Check both connectivity and the table required to serve requests.
            session.execute(select(AnalysisRecord.id).limit(0))
    except SQLAlchemyError:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ok"})
