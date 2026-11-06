from dataclasses import replace

import pytest
from sqlalchemy import func, select, update

from app.api.auth import SESSION_COOKIE
from app.manage import main
from app.repositories.database import AdminAuditRecord, UserRecord
from app.services.admin_service import assign_role
from tests.integration.test_app import make_client


def change_role(client, role="admin", apply=True, subject="test-subject", issuer=None):
    return assign_role(client.app.state.session_factory,
                       issuer or client.app.state.container.settings.cognito_issuer,
                       subject, role, actor="operator:test", reason="Administrator setup", apply=apply)


def promote(client):
    change_role(client)
    return client.sign_in()


def model_form(version=0, **overrides):
    return {"default": "test-model", "models": ["test-model"], "version": version,
            "reason": "Reviewed model selection", **overrides}


def test_signup_regular_user_and_admin_routes_deny_anonymous_and_forged_role(tmp_path):
    with make_client(tmp_path, authenticate=False) as client:
        assert client.get("/admin", follow_redirects=False).status_code == 303
        assert client.post("/admin/models", data=model_form(), follow_redirects=False).status_code == 303
        principal = client.sign_in()
        assert principal.role == "user"
        assert client.get("/admin", headers={"x-role": "admin"}).status_code == 403
        assert client.post("/admin/models", data={**model_form(), "role": "admin"}).status_code == 403
        assert 'href="/admin"' not in client.get("/").text
        assert client.post("/admin/grant-admin", data={"subject": "test-subject"}).status_code == 404


def test_exact_identity_dry_run_session_invalidation_and_audit(tmp_path):
    with make_client(tmp_path) as client:
        principal = client.sign_in()
        old_cookie = client.cookies.get(SESSION_COOKIE)
        assert change_role(client, apply=False)["before"] == "user"
        assert client.get("/admin").status_code == 403
        with pytest.raises(ValueError, match="Identity not found"):
            change_role(client, issuer="https://another-pool.example")
        with pytest.raises(ValueError, match="Identity not found"):
            change_role(client, subject="missing")
        change_role(client)
        assert client.get("/api/v1/models").status_code == 401
        admin = client.sign_in()
        assert admin.id == principal.id and admin.role == "admin"
        assert client.get("/admin").status_code == 200
        assert 'href="/admin"' in client.get("/").text
        # Re-signing in and matching email cannot grant another identity the role.
        assert client.sign_in("another-subject", principal.email).role == "user"
        assert client.get("/admin").status_code == 403
        client.sign_in()
        change_role(client, "user")
        assert client.get("/api/v1/models").status_code == 401
        client.cookies.set(SESSION_COOKIE, old_cookie)
        assert client.get("/api/v1/models").status_code == 401
        assert client.sign_in().role == "user"
        with client.app.state.session_factory() as db:
            entries = list(db.scalars(select(AdminAuditRecord).order_by(AdminAuditRecord.created_at)))
            assert [entry.after["role"] for entry in entries] == ["admin", "user"]
            assert all(entry.actor == "operator:test" for entry in entries)


def test_disabled_user_cannot_be_promoted_and_noop_does_not_duplicate_audit(tmp_path):
    with make_client(tmp_path) as client:
        promote(client)
        change_role(client)
        with client.app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(AdminAuditRecord)) == 1
            db.execute(update(UserRecord).values(disabled=True))
            db.commit()
        with pytest.raises(ValueError, match="disabled"):
            change_role(client)
        # Revocation is available even if the user has been disabled.
        change_role(client, "user")


def test_role_rechecked_each_request_and_within_write_transaction(tmp_path):
    with make_client(tmp_path) as client:
        principal = promote(client)
        with client.app.state.session_factory() as db:
            db.execute(update(UserRecord).where(UserRecord.id == principal.id).values(role="user"))
            db.commit()
        assert client.get("/admin").status_code == 403
        with pytest.raises(PermissionError):
            client.app.state.container.admin_service.save_models(
                replace(principal, role="admin"), "test-model", ["test-model"], 0, "Forged principal")


def test_models_saved_enforced_persisted_and_audited(tmp_path):
    with make_client(tmp_path) as client:
        promote(client)
        settings = client.app.state.container.settings
        settings.openai_allowed_models = "test-model,second-model"
        response = client.post("/admin/models", data=model_form(
            default="second-model", models=["second-model"], reason="<script>alert(1)</script>"),
            follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/api/v1/models").json() == {"default": "second-model", "models": ["second-model"]}
        assert '<option value="second-model"' in client.get("/").text
        assert client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"}).status_code == 422
        result = client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "second-model"})
        assert result.status_code == 201 and result.json()["model"] == "second-model"
        page = client.get("/admin")
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text
        with client.app.state.session_factory() as db:
            entry = db.scalar(select(AdminAuditRecord).where(AdminAuditRecord.action == "models.change"))
            assert entry.before["default"] == "test-model"
            assert entry.after == {"default": "second-model", "models": ["second-model"]}
    with make_client(tmp_path, settings=settings) as client:
        assert client.get("/api/v1/models").json()["default"] == "second-model"


def test_csrf_validation_conflicts_and_no_partial_audit(tmp_path):
    with make_client(tmp_path) as client:
        promote(client)
        csrf = client.headers.pop("x-csrf-token")
        assert client.post("/admin/models", data=model_form()).status_code == 403
        client.headers["x-csrf-token"] = csrf
        assert client.post("/admin/models", data=model_form(), headers={"origin": "https://evil.example"}).status_code == 403
        for overrides in ({"default": "forbidden"}, {"models": ["forbidden"]}, {"reason": " "}, {"models": []}):
            assert client.post("/admin/models", data=model_form(**overrides)).status_code == 422
        assert client.post("/admin/models", data=model_form(), follow_redirects=False).status_code == 303
        assert client.post("/admin/models", data=model_form()).status_code == 409
        with client.app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(AdminAuditRecord)) == 2


def test_deployment_allowlist_ceiling_and_admin_recovery(tmp_path):
    with make_client(tmp_path) as client:
        promote(client)
        client.post("/admin/models", data=model_form(), follow_redirects=False)
        client.app.state.container.settings.openai_default_model = "new-model"
        client.app.state.container.settings.openai_allowed_models = "new-model"
        assert client.get("/api/v1/models").status_code == 503
        assert client.post("/api/v1/analyses", json={"symbol": "AAPL", "model": "test-model"}).status_code == 503
        assert client.get("/admin").status_code == 200
        assert client.post("/admin/models", data=model_form(
            version=1, default="new-model", models=["new-model"]), follow_redirects=False).status_code == 303
        assert client.get("/api/v1/models").json()["models"] == ["new-model"]


def test_cli_preview_apply_wrong_pool_and_listing(tmp_path, monkeypatch, capsys):
    with make_client(tmp_path) as client:
        monkeypatch.setattr("app.manage.get_settings", lambda: client.app.state.container.settings)
        main(["list-users"])
        output = capsys.readouterr().out
        assert "test-subject" in output and client.app.state.container.settings.cognito_issuer in output
        args = ["grant-admin", "--issuer", client.app.state.container.settings.cognito_issuer,
                "--subject", "test-subject", "--reason", "Bootstrap administrator"]
        main(args)
        assert "Preview only" in capsys.readouterr().out
        assert client.get("/admin").status_code == 403
        with pytest.raises(SystemExit):
            main(["grant-admin", "--issuer", "https://wrong.example", "--subject", "test-subject", "--reason", "test"])
        main([*args, "--apply"])
        assert "Applied" in capsys.readouterr().out
        assert client.sign_in().role == "admin"
        args[0] = "revoke-admin"
        main([*args, "--apply"])
        assert client.sign_in().role == "user"
