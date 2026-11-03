from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.auth import LOGIN_COOKIE, SESSION_COOKIE
from app.services.auth_service import AuthenticationError

router = APIRouter(tags=["accounts"])
templates = Jinja2Templates(directory="app/templates")


def auth_service(request):
    return request.app.state.container.auth_service


def set_cookie(response, request, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=auth_service(request).settings.secure_cookies,
                        samesite="lax", path="/")


def login_page(request, error=None, status_code=200):
    return templates.TemplateResponse(request=request, name="login.html", context={
        "error": error, "configured": auth_service(request).settings.auth_configured,
    }, status_code=status_code)


@router.get("/login")
def login(request: Request, start: bool = False):
    if request.state.user:
        return RedirectResponse("/", 303)
    if start:
        return begin(request, "login")
    return login_page(request)


def begin(request, mode):
    try:
        url, state = auth_service(request).begin_login(mode)
    except AuthenticationError as exc:
        return login_page(request, str(exc), 503)
    response = RedirectResponse(url, 303)
    set_cookie(response, request, LOGIN_COOKIE, state, 600)
    return response


@router.get("/signup")
def signup(request: Request):
    if request.state.user:
        return RedirectResponse("/", 303)
    return begin(request, "signup")


@router.get("/forgot-password")
def forgot_password(request: Request):
    return begin(request, "recovery")


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    try:
        if error or not code or not auth_service(request).settings.auth_configured:
            raise AuthenticationError("Sign-in was not completed. Please try again.")
        cookie, _ = auth_service(request).finish_login(
            code, state, request.cookies.get(LOGIN_COOKIE, ""),
            request.cookies.get(SESSION_COOKIE, ""),
        )
        response = RedirectResponse("/", 303)
        set_cookie(response, request, SESSION_COOKIE, cookie,
                   auth_service(request).settings.session_lifetime_seconds)
    except AuthenticationError as exc:
        response = login_page(request, str(exc), 400)
    response.delete_cookie(LOGIN_COOKIE, path="/")
    return response


@router.get("/account")
def account(request: Request):
    return templates.TemplateResponse(request=request, name="account.html")


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: Annotated[str, Form(min_length=1, max_length=256)],
    new_password: Annotated[str, Form(min_length=12, max_length=256)],
    confirm_password: Annotated[str, Form(min_length=12, max_length=256)],
):
    try:
        if new_password != confirm_password:
            raise AuthenticationError("The new passwords do not match.")
        auth_service(request).change_password(request.state.user, current_password, new_password)
    except AuthenticationError as exc:
        return templates.TemplateResponse(request=request, name="account.html",
                                          context={"error": str(exc)}, status_code=400)
    response = RedirectResponse(auth_service(request).logout_url(), 303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/logout")
def logout(request: Request):
    auth_service(request).sign_out(request.state.user)
    response = RedirectResponse(auth_service(request).logout_url(), 303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
