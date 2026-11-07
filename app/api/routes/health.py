from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.repositories.database import (
    AdminAuditRecord, AnalysisRecord, AppSettingRecord, AuthSessionRecord, InvitationRecord,
    LoginAttemptRecord, SignupThrottleRecord, UserRecord,
)

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    try:
        with request.app.state.session_factory() as session:
            # Check both connectivity and the table required to serve requests.
            session.execute(select(AnalysisRecord.id, AnalysisRecord.user_id).limit(0))
            for model in (UserRecord, AuthSessionRecord, LoginAttemptRecord, AppSettingRecord, AdminAuditRecord,
                          InvitationRecord, SignupThrottleRecord):
                session.execute(select(model).limit(0))
    except SQLAlchemyError:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    if not request.app.state.container.settings.auth_configured:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ok"})
