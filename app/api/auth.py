"""Default-deny access gate shared by HTML pages and API routes."""
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

SESSION_COOKIE = "stockpilot_session"
LOGIN_COOKIE = "stockpilot_login"
PUBLIC_PATHS = {"/health", "/ready", "/login", "/signup", "/forgot-password", "/auth/callback"}


class AccountAccessMiddleware:
    def __init__(self, app, auth_service):
        self.app = app
        self.auth = auth_service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        scope.setdefault("state", {})["user"] = None
        path = scope["path"]
        is_static = path.startswith("/static/")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", (
                        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; "
                        f"form-action 'self' {self.auth.settings.cognito_domain}"
                    ).encode()),
                ])
                if not is_static:
                    headers.append((b"cache-control", b"no-store"))
                if self.auth.settings.secure_cookies:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": headers}
            await send(message)

        if path in {"/health", "/ready"} or is_static:
            return await self.app(scope, receive, secure_send)
        try:
            principal = await run_in_threadpool(
                self.auth.authenticate, request.cookies.get(SESSION_COOKIE, ""))
        except SQLAlchemyError:
            return await JSONResponse({"detail": "Account access is temporarily unavailable."}, 503)(
                scope, receive, secure_send)
        scope["state"]["user"] = principal
        if principal is None and path not in PUBLIC_PATHS:
            response = (JSONResponse({"detail": "Sign in to access this feature."}, 401)
                        if path.startswith("/api/") else RedirectResponse("/login", status_code=303))
            return await response(scope, receive, secure_send)
        return await self.app(scope, receive, secure_send)


async def validate_csrf(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"} or not getattr(request.state, "user", None):
        return
    origin = request.headers.get("origin")
    if origin and origin != request.app.state.container.settings.app_base_url:
        raise HTTPException(403, "Request origin did not match.")
    token = request.headers.get("x-csrf-token", "")
    if not token and request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        token = str((await request.form()).get("csrf_token", ""))
    if not token or not secrets.compare_digest(token, request.state.user.csrf_token):
        raise HTTPException(403, "Your form expired. Reload the page and try again.")
