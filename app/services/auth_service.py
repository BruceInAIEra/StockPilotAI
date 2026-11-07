"""Cognito OIDC and opaque, revocable application sessions.

Identity comes only from verified Cognito claims, never from a submitted email or ID.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import jwt
from cryptography.fernet import Fernet
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.repositories.database import AuthSessionRecord, InvitationRecord, LoginAttemptRecord, UserRecord
from app.services.invitation_service import InvitationError, InvitationService, signup_policy
from app.services.admin_service import audit


class AuthenticationError(Exception):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    email: str
    expires_at: datetime
    access_token: str
    refresh_token: str = ""


@dataclass(frozen=True)
class Principal:
    id: str
    email: str
    csrf_token: str
    session_hash: str
    role: str = "user"


class CognitoProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwks = jwt.PyJWKClient(
            f"{settings.cognito_issuer}/.well-known/jwks.json", timeout=10,
        )

    def exchange(self, code: str, verifier: str, nonce: str) -> Identity:
        settings = self.settings
        data = {
            "grant_type": "authorization_code", "client_id": settings.cognito_client_id,
            "code": code, "code_verifier": verifier,
            "redirect_uri": f"{settings.app_base_url}/auth/callback",
        }
        auth = (httpx.BasicAuth(settings.cognito_client_id,
                               settings.cognito_client_secret.get_secret_value())
                if settings.cognito_client_secret else None)
        try:
            with httpx.Client(timeout=15) as client:
                response = client.post(f"{settings.cognito_domain}/oauth2/token", data=data, auth=auth)
            response.raise_for_status()
            tokens = response.json()
            raw_id = tokens["id_token"]
            claims = jwt.decode(
                raw_id, self.jwks.get_signing_key_from_jwt(raw_id).key,
                algorithms=["RS256"], audience=settings.cognito_client_id,
                issuer=settings.cognito_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "token_use"]},
            )
            if (claims["token_use"] != "id" or not secrets.compare_digest(claims["nonce"], nonce)
                    or claims.get("email_verified") is not True or not claims.get("email")):
                raise AuthenticationError("A verified email is required. Please sign in again.")
            access = tokens["access_token"]
            # Bind the access token to the signed ID token when Cognito supplies at_hash.
            if "at_hash" in claims and not secrets.compare_digest(
                claims["at_hash"], b64(hashlib.sha256(access.encode()).digest()[:16])
            ):
                raise AuthenticationError("Sign-in could not be verified. Please try again.")
            return Identity(
                issuer=claims["iss"], subject=claims["sub"], email=claims["email"],
                expires_at=datetime.fromtimestamp(claims["exp"], UTC),
                access_token=access, refresh_token=tokens.get("refresh_token", ""),
            )
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise AuthenticationError("Sign-in could not be verified. Please try again.") from exc

    def change_password(self, access_token: str, previous: str, proposed: str) -> None:
        try:
            with httpx.Client(timeout=15) as client:
                response = client.post(
                    f"https://cognito-idp.{self.settings.cognito_region}.amazonaws.com/",
                    headers={"X-Amz-Target": "AWSCognitoIdentityProviderService.ChangePassword",
                             "Content-Type": "application/x-amz-json-1.1"},
                    json={"AccessToken": access_token, "PreviousPassword": previous,
                          "ProposedPassword": proposed},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthenticationError(
                "Password could not be changed. Check your current password and the password requirements, or sign in again."
            ) from exc

    def revoke(self, refresh_token: str) -> None:
        if not refresh_token:
            return
        data = {"token": refresh_token, "client_id": self.settings.cognito_client_id}
        auth = (httpx.BasicAuth(self.settings.cognito_client_id,
                               self.settings.cognito_client_secret.get_secret_value())
                if self.settings.cognito_client_secret else None)
        try:
            with httpx.Client(timeout=10) as client:
                client.post(f"{self.settings.cognito_domain}/oauth2/revoke", data=data, auth=auth)
        except httpx.HTTPError:
            # Local session revocation always completes, even during a Cognito outage.
            pass


class AuthService:
    def __init__(self, factory, settings: Settings, provider=None):
        self.factory = factory
        self.settings = settings
        self.provider = provider or CognitoProvider(settings)
        self.invitations = InvitationService(factory, settings)
        self.cipher = (Fernet(settings.session_encryption_key.get_secret_value().encode())
                       if settings.session_encryption_key else None)

    def begin_login(self, mode: str = "login", invitation_code: str = "") -> tuple[str, str]:
        if not self.settings.auth_configured:
            raise AuthenticationError("Account access is being configured. Please try again later.")
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        now = datetime.now(UTC)
        with self.factory() as db:
            db.execute(delete(LoginAttemptRecord).where(LoginAttemptRecord.expires_at <= now))
            db.execute(delete(AuthSessionRecord).where(AuthSessionRecord.expires_at <= now))
            invitation_id = None
            if mode == "signup" and (signup_policy(db, self.settings).required or invitation_code):
                try:
                    if len(invitation_code) > 128:
                        raise InvitationError("A valid, unused invitation code is required.")
                    invitation_id = self.invitations.validate(db, invitation_code)
                except InvitationError as exc:
                    raise AuthenticationError(str(exc)) from exc
            db.add(LoginAttemptRecord(invitation_id=invitation_id, state_hash=digest(state), nonce=nonce, verifier=verifier,
                                      expires_at=now + timedelta(minutes=10)))
            db.commit()
        params = {
            "response_type": "code", "client_id": self.settings.cognito_client_id,
            "redirect_uri": f"{self.settings.app_base_url}/auth/callback",
            "scope": "openid email aws.cognito.signin.user.admin",
            "state": state, "nonce": nonce,
            "code_challenge": b64(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
            # Require credentials even if another device still has a Cognito cookie.
            "prompt": "login",
        }
        path = {"login": "/oauth2/authorize", "signup": "/signup",
                "recovery": "/forgotPassword"}[mode]
        return f"{self.settings.cognito_domain}{path}?{urlencode(params)}", state

    def finish_login(self, code: str, state: str, cookie: str, old_cookie: str = "") -> tuple[str, Principal]:
        if not state or not cookie or not secrets.compare_digest(state, cookie):
            raise AuthenticationError("Sign-in expired or could not be verified. Please try again.")
        with self.factory() as db:
            attempt = db.execute(delete(LoginAttemptRecord).where(
                LoginAttemptRecord.state_hash == digest(state),
                LoginAttemptRecord.expires_at > datetime.now(UTC),
            ).returning(LoginAttemptRecord.nonce, LoginAttemptRecord.verifier, LoginAttemptRecord.invitation_id)).first()
            db.commit()  # Consume before external exchange: a callback is never reusable.
        if not attempt:
            raise AuthenticationError("Sign-in expired. Please try again.")
        identity = self.provider.exchange(code, attempt.verifier, attempt.nonce)
        return self.establish_session(identity, old_cookie, invitation_id=attempt.invitation_id)

    def establish_session(self, identity: Identity, old_cookie: str = "", *, invitation_id: str | None = None) -> tuple[str, Principal]:
        if not self.cipher:
            raise AuthenticationError("Account access is unavailable.")
        if identity.issuer != self.settings.cognito_issuer:
            raise AuthenticationError("Identity provider did not match.")
        expires = min(identity.expires_at, datetime.now(UTC) + timedelta(
            seconds=self.settings.session_lifetime_seconds))
        if expires <= datetime.now(UTC):
            raise AuthenticationError("Sign-in expired. Please try again.")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        # The unique identity constraint also protects concurrent first sign-ins.
        for retry in range(2):
            try:
                with self.factory() as db:
                    user = db.scalar(select(UserRecord).where(
                        UserRecord.issuer == identity.issuer, UserRecord.subject == identity.subject))
                    if user is None:
                        if signup_policy(db, self.settings).required and invitation_id is None:
                            raise AuthenticationError("An invitation is required. Please use Create account to register.")
                        user = UserRecord(id=str(uuid4()), issuer=identity.issuer,
                                          subject=identity.subject, email=identity.email)
                        db.add(user)
                        db.flush()
                        if invitation_id is not None:
                            redeemed = db.execute(update(InvitationRecord).where(
                                InvitationRecord.id == invitation_id,
                                InvitationRecord.used_at.is_(None),
                                InvitationRecord.deactivated_at.is_(None),
                            ).values(used_at=datetime.now(UTC), used_by=user.id))
                            if redeemed.rowcount != 1:
                                raise AuthenticationError("This invitation is unavailable. Please request another code.")
                            audit(db, user.id, "invitation.redeem", invitation_id,
                                  {"status": "available"}, {"status": "used", "user_id": user.id},
                                  "Registered with invitation")
                    if user.disabled:
                        raise AuthenticationError("This account is unavailable.")
                    user.email = identity.email
                    if old_cookie:
                        db.execute(delete(AuthSessionRecord).where(
                            AuthSessionRecord.token_hash == digest(old_cookie)))
                    db.add(AuthSessionRecord(
                        token_hash=digest(token), user_id=user.id, csrf_token=csrf, expires_at=expires,
                        encrypted_tokens=self.cipher.encrypt(json.dumps({
                            "access_token": identity.access_token,
                            "refresh_token": identity.refresh_token,
                        }).encode()).decode(),
                    ))
                    principal = Principal(user.id, user.email, csrf, digest(token), user.role)
                    db.commit()
                    return token, principal
            except IntegrityError:
                if retry:
                    raise
        raise AuthenticationError("Sign-in could not be completed.")

    def authenticate(self, cookie: str) -> Principal | None:
        if not cookie or len(cookie) > 128 or not self.settings.auth_configured:
            return None
        with self.factory() as db:
            row = db.execute(select(AuthSessionRecord, UserRecord).join(
                UserRecord, UserRecord.id == AuthSessionRecord.user_id).where(
                AuthSessionRecord.token_hash == digest(cookie),
                AuthSessionRecord.expires_at > datetime.now(UTC),
                UserRecord.disabled.is_(False),
            )).first()
            if not row:
                return None
            session, user = row
            return Principal(user.id, user.email, session.csrf_token, session.token_hash, user.role)

    def _tokens(self, principal: Principal) -> dict:
        with self.factory() as db:
            record = db.get(AuthSessionRecord, principal.session_hash)
            if record is None or utc(record.expires_at) <= datetime.now(UTC):
                raise AuthenticationError("Your session expired. Please sign in again.")
            return json.loads(self.cipher.decrypt(record.encrypted_tokens.encode()))

    def sign_out(self, principal: Principal, all_sessions: bool = False) -> None:
        tokens = self._tokens(principal)
        with self.factory() as db:
            condition = (AuthSessionRecord.user_id == principal.id if all_sessions else
                         AuthSessionRecord.token_hash == principal.session_hash)
            db.execute(delete(AuthSessionRecord).where(condition))
            db.commit()
        self.provider.revoke(tokens.get("refresh_token", ""))

    def change_password(self, principal: Principal, previous: str, proposed: str) -> None:
        tokens = self._tokens(principal)
        self.provider.change_password(tokens["access_token"], previous, proposed)
        self.sign_out(principal, all_sessions=True)

    def logout_url(self) -> str:
        return f"{self.settings.cognito_domain}/logout?{urlencode({'client_id': self.settings.cognito_client_id, 'logout_uri': self.settings.app_base_url + '/login'})}"
