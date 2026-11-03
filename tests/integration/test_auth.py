from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update

from app.api.auth import LOGIN_COOKIE, SESSION_COOKIE
from app.repositories.database import AnalysisRecord, AuthSessionRecord, UserRecord
from app.services.auth_service import Identity, digest
from tests.integration.test_app import make_client


def test_anonymous_can_only_access_account_entry_points(tmp_path):
    with make_client(tmp_path, authenticate=False) as client:
        for path in ("/", "/account", "/analyses/guess", "/docs", "/openapi.json"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/login"
        for path in ("/api/v1/models", "/api/v1/analyses", "/api/v1/analyses/guess"):
            assert client.get(path).status_code == 401
        assert client.post("/api/v1/analyses", json={}).status_code == 401
        assert client.post("/analyze", data={}, follow_redirects=False).status_code == 303
        page = client.get("/login")
        assert page.status_code == 200
        assert "Create account" in page.text and "Forgot your password?" in page.text
        assert "analysis-form" not in page.text
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["x-frame-options"] == "DENY"
        assert client.get("/static/css/app.css").status_code == 200


def test_login_pkce_state_rotation_replay_and_verified_identity(tmp_path):
    with make_client(tmp_path, authenticate=False) as client:
        response = client.get("/signup", follow_redirects=False)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert "nonce" in query and "code_challenge" in query
        assert "HttpOnly" in response.headers["set-cookie"]
        state = query["state"][0]
        assert client.get("/auth/callback", params={"code": "valid-code", "state": "forged"}).status_code == 400
        # The forged callback clears the browser's attempt cookie; restore the bound cookie.
        client.cookies.set(LOGIN_COOKIE, state)
        response = client.get("/auth/callback", params={"code": "valid-code", "state": state}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
        cookie = client.cookies.get(SESSION_COOKIE)
        assert cookie and "test-access" not in cookie
        assert client.get("/account").status_code == 200
        client.cookies.set(LOGIN_COOKIE, state)
        assert client.get("/auth/callback", params={"code": "valid-code", "state": state}).status_code == 400
        auth = client.app.state.container.auth_service
        with auth.factory() as db:
            session = db.get(AuthSessionRecord, digest(cookie))
            assert "test-access" not in session.encrypted_tokens
            assert "test-refresh" not in session.encrypted_tokens
            assert session.token_hash != cookie


def test_expired_login_attempt_and_provider_rejection_cannot_log_in(tmp_path):
    from app.repositories.database import LoginAttemptRecord
    with make_client(tmp_path, authenticate=False) as client:
        client.get("/login?start=true", follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        with client.app.state.session_factory() as db:
            db.execute(update(LoginAttemptRecord).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            db.commit()
        assert client.get("/auth/callback", params={"code": "valid-code", "state": state}).status_code == 400
        client.get("/login?start=true", follow_redirects=False)
        state = client.cookies.get(LOGIN_COOKIE)
        assert client.get("/auth/callback", params={"code": "bad-code", "state": state}).status_code == 400
        assert client.get("/api/v1/analyses").status_code == 401


def test_private_history_detail_and_unassigned_legacy_records(tmp_path):
    with make_client(tmp_path) as client:
        first = client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"}).json()
        legacy = client.post("/api/v1/analyses", json={"symbol": "MSFT", "model": "test-model"}).json()
        with client.app.state.session_factory() as db:
            db.execute(update(AnalysisRecord).where(AnalysisRecord.id == legacy["id"]).values(user_id=None))
            db.commit()
        assert len(client.get("/api/v1/analyses").json()) == 1
        assert client.get(f"/api/v1/analyses/{legacy['id']}").status_code == 404
        client.sign_in("other-subject", "other@example.com")
        assert client.get("/api/v1/analyses").json() == []
        assert client.get(f"/api/v1/analyses/{first['id']}").status_code == 404
        assert client.get(f"/analyses/{first['id']}").status_code == 404
        assert first["id"] not in client.get("/").text
        # A submitted owner ID is never used as identity.
        second = client.post("/api/v1/analyses", json={
            "symbol": "GOOG", "model": "test-model", "user_id": "test-subject",
        })
        assert second.status_code == 422
        client.sign_in()
        assert [row["id"] for row in client.get("/api/v1/analyses").json()] == [first["id"]]


def test_cookie_session_requires_csrf_and_same_origin(tmp_path):
    with make_client(tmp_path) as client:
        csrf = client.headers.pop("x-csrf-token")
        assert client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"}).status_code == 403
        form = {"symbol": "AAPL", "horizon": "medium_term", "position": "not_owned", "model": "test-model"}
        assert client.post("/analyze", data=form).status_code == 403
        assert client.post("/logout").status_code == 403
        assert client.post("/analyze", data={**form, "csrf_token": "forged"}).status_code == 403
        assert client.post("/analyze", data={**form, "csrf_token": csrf},
                           headers={"origin": "https://attacker.example"}).status_code == 403
        response = client.post("/analyze", data={**form, "csrf_token": csrf}, follow_redirects=False)
        assert response.status_code == 303
        assert client.get(response.headers["location"]).status_code == 200


def test_logout_revokes_cookie_and_replay(tmp_path):
    with make_client(tmp_path) as client:
        old = client.cookies.get(SESSION_COOKIE)
        response = client.post("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert urlsplit(response.headers["location"]).path == "/logout"
        assert "Max-Age=0" in response.headers["set-cookie"]
        client.cookies.set(SESSION_COOKIE, old)
        assert client.get("/api/v1/analyses").status_code == 401


@pytest.mark.parametrize("condition", ["expired", "disabled", "forged"])
def test_invalid_sessions_are_denied(tmp_path, condition):
    with make_client(tmp_path) as client:
        with client.app.state.session_factory() as db:
            if condition == "expired":
                db.execute(update(AuthSessionRecord).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            elif condition == "disabled":
                db.execute(update(UserRecord).values(disabled=True))
            else:
                client.cookies.set(SESSION_COOKIE, "forged")
            db.commit()
        assert client.get("/api/v1/analyses").status_code == 401


def test_password_change_invalidates_all_app_sessions_and_never_echoes_passwords(tmp_path):
    with make_client(tmp_path) as client:
        old_cookie = client.cookies.get(SESSION_COOKIE)
        client.sign_in()
        new_cookie = client.cookies.get(SESSION_COOKIE)
        response = client.post("/account/password", data={
            "current_password": "secret", "new_password": "tiny", "confirm_password": "tiny",
        })
        assert response.status_code == 422
        assert "secret" not in response.text and "tiny" not in response.text
        response = client.post("/account/password", data={
            "current_password": "CorrectPassword1!", "new_password": "DifferentPassword2!",
            "confirm_password": "DifferentPassword2!",
        }, follow_redirects=False)
        assert response.status_code == 303
        assert client.app.state.container.auth_service.provider.password_changes == 1
        for cookie in (old_cookie, new_cookie):
            client.cookies.set(SESSION_COOKIE, cookie)
            assert client.get("/api/v1/analyses").status_code == 401


def test_same_email_does_not_merge_distinct_identities(tmp_path):
    with make_client(tmp_path) as client:
        first = client.sign_in("subject-one", "same@example.com")
        second = client.sign_in("subject-two", "same@example.com")
        assert first.id != second.id


def test_auth_configuration_missing_fails_closed(tmp_path):
    with make_client(tmp_path, authenticate=False) as client:
        client.app.state.container.settings.cognito_client_id = ""
        assert client.get("/ready").status_code == 503
        assert client.get("/api/v1/analyses").status_code == 401
        assert client.get("/signup", follow_redirects=False).status_code == 503


def test_history_pagination_stays_private(tmp_path):
    from uuid import uuid4
    from app.domain.analysis import AnalysisRequest
    with make_client(tmp_path) as client:
        principal = client.sign_in()
        repository = client.app.state.container.analysis_service._repository
        for _ in range(25):
            repository.create_running(str(uuid4()), AnalysisRequest(symbol="MSFT", model="test-model"), principal.id)
        first = client.get("/history")
        assert first.text.count('class="history-row"') == 20
        assert "Older reports" in first.text
        second = client.get("/history?page=2")
        assert second.text.count('class="history-row"') == 5
        assert "Newer reports" in second.text
        assert len(client.get("/api/v1/analyses?limit=20&offset=20").json()) == 5
        client.sign_in("different-user", "other@example.com")
        assert "No analyses yet" in client.get("/history").text
        assert client.get("/api/v1/analyses?offset=20").json() == []
