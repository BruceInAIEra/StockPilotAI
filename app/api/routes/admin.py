from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.repositories.database import AppSettingRecord
from app.services.admin_service import ModelSettings, ModelsUnavailable, SettingsConflict
from app.services.invitation_service import InvitationError


def require_admin(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Sign in to access this feature.")
    if user.role != "admin":
        raise HTTPException(403, "Admin access required.")
    return user


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")


def render(request, error=None, status_code=200, generated_code=None, page=1):
    service = request.app.state.container.admin_service
    try:
        current = service.model_settings()
    except ModelsUnavailable:
        # Keep recovery available if a deployment removed all previously enabled models.
        with service.factory() as db:
            row = db.get(AppSettingRecord, "models")
            current = ModelSettings(service.settings.openai_default_model, (), row.version if row else 0)
    return templates.TemplateResponse(request=request, name="admin.html", context={
        "current": current, "available_models": service.settings.allowed_models,
        "audit": service.recent_audit(), "error": error,
        "signup_policy": request.app.state.container.auth_service.invitations.policy(),
        "invitations": request.app.state.container.auth_service.invitations.recent(page),
        "generated_code": generated_code, "page": page,
    }, status_code=status_code)


@router.get("")
def admin_page(request: Request, page: int = 1):
    return render(request, page=max(1, page))


@router.post("/models")
def save_models(request: Request, default: Annotated[str, Form()],
                models: Annotated[list[str], Form()], version: Annotated[int, Form(ge=0)],
                reason: Annotated[str, Form(max_length=500)]):
    try:
        request.app.state.container.admin_service.save_models(
            request.state.user, default, models, version, reason)
    except PermissionError as exc:
        raise HTTPException(403, "Admin access required.") from exc
    except SettingsConflict as exc:
        return render(request, str(exc), 409)
    except ValueError as exc:
        return render(request, str(exc), 422)
    return RedirectResponse("/admin", 303)


@router.post("/signup-policy")
def save_signup_policy(request: Request, version: Annotated[int, Form(ge=0)],
                       invite_only: Annotated[bool, Form()] = False):
    try:
        request.app.state.container.auth_service.invitations.save_policy(
            request.state.user, invite_only, version)
    except PermissionError as exc:
        raise HTTPException(403, "Admin access required.") from exc
    except SettingsConflict as exc:
        return render(request, str(exc), 409)
    return RedirectResponse("/admin", 303)


@router.post("/invitations")
def generate_invitation(request: Request):
    try:
        code = request.app.state.container.auth_service.invitations.generate(request.state.user)
    except PermissionError as exc:
        raise HTTPException(403, "Admin access required.") from exc
    return render(request, generated_code=code)


@router.post("/invitations/{invitation_id}/deactivate")
def deactivate_invitation(request: Request, invitation_id: str):
    try:
        request.app.state.container.auth_service.invitations.deactivate(request.state.user, invitation_id)
    except PermissionError as exc:
        raise HTTPException(403, "Admin access required.") from exc
    except InvitationError as exc:
        return render(request, str(exc), 409)
    return RedirectResponse("/admin", 303)
