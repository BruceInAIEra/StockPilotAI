from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
import re

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from app.api.auth import LOGIN_COOKIE
from app.repositories.database import (
    AdminAuditRecord, AuthSessionRecord, InvitationRecord, LoginAttemptRecord, UserRecord,
)
from app.services.auth_service import AuthenticationError, Identity
from app.services.invitation_service import SignupRateLimited
from tests.integration.test_admin import promote
from tests.integration.test_app import make_client


def identity(client, subject="new-user"):
    return Identity(client.app.state.container.settings.cognito_issuer, subject,
                    f"{subject}@example.com", datetime.now(UTC) + timedelta(hours=1), "access")


def setup_invitation(client):
    admin = promote(client)
    service = client.app.state.container.auth_service.invitations
    code = service.generate(admin)
    service.save_policy(admin, True, 0)
    with client.app.state.session_factory() as db:
        invitation_id = db.scalar(select(InvitationRecord.id))
    return admin, code, invitation_id


def anonymous(client):
    client.cookies.clear()
    client.headers.pop("x-csrf-token", None)


def signup_form(client, code=""):
    page = client.get("/signup", follow_redirects=False)
    token = re.search(r'name="signup_csrf" value="([^"]+)"', page.text)[1]
    return {"signup_csrf": token, "invitation_code": code}


def test_missing_and_invalid_codes_block_before_cognito_and_no_secret_leak(tmp_path, monkeypatch):
    with make_client(tmp_path) as client:
        _, code, _ = setup_invitation(client)
        anonymous(client)
        provider = client.app.state.container.auth_service.provider
        monkeypatch.setattr(provider, "exchange", lambda *args: pytest.fail("Must not call Cognito"))
        for bad in ("", "invalid-code", "x" * 129):
            response = client.post("/signup", data=signup_form(client, bad), follow_redirects=False)
            assert response.status_code == 400
            assert "location" not in response.headers
            assert bad == "" or bad not in response.text
        with client.app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(LoginAttemptRecord)) == 0
            row = db.scalar(select(InvitationRecord))
            assert row.used_at is None and row.code_hash != code
            assert code not in str(db.scalars(select(AdminAuditRecord.after)).all())


def test_signup_requires_csrf_origin_and_throttles_across_service_instances(tmp_path):
    from app.services.invitation_service import InvitationService
    with make_client(tmp_path) as client:
        setup_invitation(client)
        anonymous(client)
        assert client.post("/signup", data={"invitation_code": "bad"}).status_code == 403
        form = signup_form(client, "bad")
        assert client.post("/signup", data=form, headers={"origin": "https://evil.example"}).status_code == 403
        for _ in range(5):
            assert client.post("/signup", data=signup_form(client, "bad")).status_code == 400
        assert client.post("/signup", data=signup_form(client, "bad")).status_code == 429
        auth = client.app.state.container.auth_service
        other = InvitationService(auth.factory, auth.settings)
        with pytest.raises(SignupRateLimited):
            other.check_rate_limit("testclient")


def test_successful_signup_redeems_once_and_existing_user_login_does_not_need_code(tmp_path):
    with make_client(tmp_path) as client:
        _, code, invitation_id = setup_invitation(client)
        anonymous(client)
        # Different from the already-existing administrator identity.
        auth = client.app.state.container.auth_service
        auth.provider.exchange = lambda *args: identity(client)
        response = client.post("/signup", data=signup_form(client, code), follow_redirects=False)
        assert response.status_code == 303
        assert code not in response.headers["location"]
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        response = client.get("/auth/callback", params={"code": "valid", "state": state}, follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/account").status_code == 200
        with auth.factory() as db:
            row = db.get(InvitationRecord, invitation_id)
            assert row.used_at is not None
            user = db.get(UserRecord, row.used_by)
            assert user.subject == "new-user" and user.role == "user"
            assert db.scalar(select(func.count()).select_from(AuthSessionRecord).where(
                AuthSessionRecord.user_id == user.id)) == 1
        anonymous(client)
        assert client.post("/signup", data=signup_form(client, code)).status_code == 400
        response = client.get("/login?start=true", follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        assert client.get("/auth/callback", params={"code": "valid", "state": state}, follow_redirects=False).status_code == 303
        with pytest.raises(AuthenticationError, match="unavailable"):
            auth.establish_session(identity(client, "another-user"), invitation_id=invitation_id)


def test_provider_failure_expiry_abandonment_and_callback_replay_leave_code_reusable(tmp_path):
    with make_client(tmp_path) as client:
        _, code, invitation_id = setup_invitation(client)
        anonymous(client)
        auth = client.app.state.container.auth_service
        # Abandon one attempt and allow a second attempt with the same code.
        auth.begin_login("signup", code)
        auth.begin_login("signup", code)
        client.post("/signup", data=signup_form(client, code), follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        assert client.get("/auth/callback", params={"code": "bad-code", "state": state}).status_code == 400
        client.cookies.set(LOGIN_COOKIE, state, domain="testserver.local")
        assert client.get("/auth/callback", params={"code": "valid-code", "state": state}).status_code == 400
        client.post("/signup", data=signup_form(client, code), follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        with auth.factory() as db:
            db.execute(update(LoginAttemptRecord).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            db.commit()
        assert client.get("/auth/callback", params={"code": "valid-code", "state": state}).status_code == 400
        with auth.factory() as db:
            assert db.get(InvitationRecord, invitation_id).used_at is None
        # A fresh registration succeeds using the original code.
        auth.provider.exchange = lambda *args: identity(client)
        client.post("/signup", data=signup_form(client, code), follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        assert client.get("/auth/callback", params={"code": "valid", "state": state}, follow_redirects=False).status_code == 303


def test_session_database_failure_rolls_back_user_redemption_and_audit(tmp_path):
    with make_client(tmp_path) as client:
        _, _, invitation_id = setup_invitation(client)
        auth = client.app.state.container.auth_service
        engine = auth.factory.kw["bind"]
        def fail_session(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO auth_sessions"):
                raise OperationalError(statement, {}, Exception("Injected session save failure"))
        event.listen(engine, "before_cursor_execute", fail_session)
        try:
            with pytest.raises(OperationalError):
                auth.establish_session(identity(client), invitation_id=invitation_id)
        finally:
            event.remove(engine, "before_cursor_execute", fail_session)
        with auth.factory() as db:
            assert db.get(InvitationRecord, invitation_id).used_at is None
            assert db.scalar(select(UserRecord).where(UserRecord.subject == "new-user")) is None
            assert db.scalar(select(AdminAuditRecord).where(AdminAuditRecord.action == "invitation.redeem")) is None
        auth.establish_session(identity(client), invitation_id=invitation_id)


def test_direct_login_bypass_rejected_and_deactivation_rechecked_at_callback(tmp_path):
    with make_client(tmp_path) as client:
        admin, code, invitation_id = setup_invitation(client)
        auth = client.app.state.container.auth_service
        auth.provider.exchange = lambda *args: identity(client)
        anonymous(client)
        client.get("/login?start=true", follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        assert client.get("/auth/callback", params={"code": "valid", "state": state}).status_code == 400
        client.post("/signup", data=signup_form(client, code), follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        auth.invitations.deactivate(admin, invitation_id)
        assert client.get("/auth/callback", params={"code": "valid", "state": state}).status_code == 400
        with auth.factory() as db:
            row = db.get(InvitationRecord, invitation_id)
            assert row.used_at is None and row.deactivated_at is not None
            assert db.scalar(select(UserRecord).where(UserRecord.subject == "new-user")) is None
        assert client.post("/signup", data=signup_form(client, code)).status_code == 400


def test_simultaneous_redemptions_create_only_one_user_and_session(tmp_path):
    with make_client(tmp_path) as client:
        _, _, invitation_id = setup_invitation(client)
        auth = client.app.state.container.auth_service
        barrier = Barrier(2)
        def redeem(subject):
            barrier.wait(timeout=5)
            try:
                auth.establish_session(identity(client, subject), invitation_id=invitation_id)
                return "success"
            except AuthenticationError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(redeem, ("first", "second"))) == ["rejected", "success"]
        with auth.factory() as db:
            assert db.scalar(select(func.count()).select_from(UserRecord).where(
                UserRecord.subject.in_(["first", "second"]))) == 1
            assert db.scalar(select(func.count()).select_from(AdminAuditRecord).where(
                AdminAuditRecord.action == "invitation.redeem")) == 1


def test_admin_controls_authorization_csrf_policy_conflicts_and_history(tmp_path):
    with make_client(tmp_path) as client:
        for path in ("/admin/signup-policy", "/admin/invitations", "/admin/invitations/id/deactivate"):
            assert client.post(path, data={"version": 0}).status_code == 403
        admin = promote(client)
        csrf = client.headers.pop("x-csrf-token")
        for path in ("/admin/signup-policy", "/admin/invitations", "/admin/invitations/id/deactivate"):
            assert client.post(path, data={"version": 0}).status_code == 403
        client.headers["x-csrf-token"] = csrf
        assert client.post("/admin/signup-policy", data={"version": 0, "invite_only": "true"}, follow_redirects=False).status_code == 303
        assert client.post("/admin/signup-policy", data={"version": 0}).status_code == 409
        page = client.post("/admin/invitations")
        code = re.search(r'id="generated-invitation" type="text" value="([^"]+)"', page.text)[1]
        assert 'id="generated-invitation"' not in client.get("/admin").text
        auth = client.app.state.container.auth_service
        with auth.factory() as db:
            invitation_id = db.scalar(select(InvitationRecord.id))
        auth.establish_session(identity(client), invitation_id=invitation_id)
        page = client.get("/admin")
        assert "Used" in page.text and "new-user@example.com" in page.text and admin.email in page.text
        assert client.post(f"/admin/invitations/{invitation_id}/deactivate").status_code == 409
        assert client.post("/admin/signup-policy", data={"version": 1}, follow_redirects=False).status_code == 303
        anonymous(client)
        assert client.get("/signup", follow_redirects=False).status_code == 303
        auth.establish_session(identity(client, "public-user"))
        settings = auth.settings
    with make_client(tmp_path, settings=settings, authenticate=False) as client:
        assert client.app.state.container.auth_service.invitations.policy().required is False


def test_environment_default_and_latest_policy_apply_at_callback(tmp_path):
    from app.core.config import Settings
    assert Settings(_env_file=None).invite_only_signup is True
    with make_client(tmp_path) as client:
        admin = promote(client)
        auth = client.app.state.container.auth_service
        # A public signup begun before an administrator closes registration cannot bypass it.
        _, state = auth.begin_login("signup")
        auth.invitations.save_policy(admin, True, 0)
        auth.provider.exchange = lambda *args: identity(client)
        with pytest.raises(AuthenticationError, match="invitation"):
            auth.finish_login("valid", state, state)
        # Admin's existing account remains usable without consuming another invitation.
        code = auth.invitations.generate(admin)
        _, state = auth.begin_login("signup", code)
        auth.provider.exchange = lambda *args: identity(client, "test-subject")
        auth.finish_login("valid", state, state)
        with auth.factory() as db:
            assert db.scalar(select(InvitationRecord)).used_at is None


def test_callback_database_failure_shows_retry_message_and_preserves_invitation(tmp_path):
    with make_client(tmp_path) as client:
        _, code, invitation_id = setup_invitation(client)
        anonymous(client)
        auth = client.app.state.container.auth_service
        auth.provider.exchange = lambda *args: identity(client)
        client.post("/signup", data=signup_form(client, code), follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        engine = auth.factory.kw["bind"]
        def fail_session(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO auth_sessions"):
                raise OperationalError(statement, {}, Exception("Database unavailable"))
        event.listen(engine, "before_cursor_execute", fail_session)
        try:
            response = client.get("/auth/callback", params={"code": "valid", "state": state})
            assert response.status_code == 503
            assert "unused invitation codes remain valid" in response.text
            assert "Database unavailable" not in response.text
        finally:
            event.remove(engine, "before_cursor_execute", fail_session)
        assert client.get("/api/v1/models").status_code == 401
        with auth.factory() as db:
            assert db.get(InvitationRecord, invitation_id).used_at is None
        client.post("/signup", data=signup_form(client, code), follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        assert client.get("/auth/callback", params={"code": "valid", "state": state}, follow_redirects=False).status_code == 303


def test_short_codes_case_insensitivity_collision_retry_and_legacy_codes(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.services.invitation_service import CODE_ALPHABET, InvitationError, code_digest
    with make_client(tmp_path) as client:
        admin, code, _ = setup_invitation(client)
        auth = client.app.state.container.auth_service
        assert len(code) == 4 and set(code) <= set(CODE_ALPHABET)
        with auth.factory() as db:
            row = db.scalar(select(InvitationRecord))
            assert row.suffix == ""
            assert auth.invitations.validate(db, f"  {code.lower()}  ") == row.id
            assert code not in str(db.scalars(select(AdminAuditRecord.after)).all())
            legacy = "AbCdEfGhIjKlMnOpQrStUvWxYz_1234567"
            legacy_id = str(uuid4())
            db.add(InvitationRecord(id=legacy_id, code_hash=code_digest(legacy), suffix=legacy[-8:],
                                    created_by=admin.id))
            db.commit()
            assert auth.invitations.validate(db, legacy) == legacy_id
            with pytest.raises(InvitationError):
                auth.invitations.validate(db, legacy.lower())
        # A collision with any existing code retries without losing history or audit atomicity.
        alternate = "2345" if code != "2345" else "6789"
        choices = iter(code + alternate)
        monkeypatch.setattr("app.services.invitation_service.secrets.choice", lambda alphabet: next(choices))
        assert auth.invitations.generate(admin) == alternate
        with auth.factory() as db:
            assert db.scalar(select(func.count()).select_from(InvitationRecord)) == 3
            assert db.scalar(select(func.count()).select_from(AdminAuditRecord).where(
                AdminAuditRecord.action == "invitation.create")) == 2


def test_global_rate_limit_blocks_distributed_guesses_and_resets(tmp_path, monkeypatch):
    from app.repositories.database import SignupThrottleRecord
    from app.services.invitation_service import InvitationService
    with make_client(tmp_path) as client:
        auth = client.app.state.container.auth_service
        service = auth.invitations
        # Keep this test independent of a ten-minute boundary on the real clock.
        import app.services.invitation_service as module
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(module, "datetime", FixedDatetime)
        for _ in range(5):
            service.check_rate_limit("blocked-client")
        for _ in range(110):
            with pytest.raises(SignupRateLimited):
                service.check_rate_limit("blocked-client")
        # Rejected requests from that client have not exhausted the global allowance.
        for i in range(95):
            service.check_rate_limit(f"client-{i}")
        other = InvitationService(auth.factory, auth.settings)
        with pytest.raises(SignupRateLimited):
            other.check_rate_limit("another-client")
        with auth.factory() as db:
            db.execute(update(SignupThrottleRecord).values(
                expires_at=datetime(2026, 11, 6, 11, 0, 0, tzinfo=UTC)))
            db.commit()
        other.check_rate_limit("another-client")
