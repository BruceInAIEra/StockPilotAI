"""Real PostgreSQL migration and API checks; no paid/external providers used."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.core.config import Settings, get_settings
from tests.integration.test_app import make_client


@pytest.fixture
def postgres_settings(monkeypatch):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL or run docker compose run --build --rm tests")
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    # Each run uses an isolated schema and never deletes existing application data.
    schema = f"test_{uuid4().hex}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", isolated_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        yield Settings(
            _env_file=None,
            app_env="production",
            invite_only_signup=False,
            database_url=isolated_url.render_as_string(hide_password=False),
            openai_api_key=None,
            openai_default_model="test-model",
            openai_allowed_models="test-model",
        )
    finally:
        get_settings.cache_clear()
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def test_migrations_and_analysis_survive_app_restart(postgres_settings, tmp_path):
    with make_client(tmp_path, settings=postgres_settings) as client:
        # PostgreSQL must never silently create tables during app startup.
        assert client.get("/ready").status_code == 503

    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # Re-running a release migration is safe.
    command.check(config)  # Migrations and SQLAlchemy metadata agree.

    with make_client(tmp_path, settings=postgres_settings) as client:
        assert client.get("/ready").status_code == 200
        response = client.post("/api/v1/analyses", json={
            "symbol": "TEST", "horizon": "long_term", "owns_stock": False,
            "model": "test-model",
        })
        assert response.status_code == 201
        saved = response.json()
        assert saved["status"] == "completed"
        assert saved["action"] == "BUY"
        assert saved["market_data"]["fundamentals"]["annual_financials"][0]["revenue"] == 1200

    with make_client(tmp_path, settings=postgres_settings) as client:
        restored = client.get(f"/api/v1/analyses/{saved['id']}")
        assert restored.status_code == 200
        assert restored.json() == saved
        assert client.get(f"/analyses/{saved['id']}").status_code == 200
        assert len(client.get("/api/v1/analyses").json()) == 1

    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_admin_role_settings_and_concurrent_updates(postgres_settings, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.services.admin_service import SettingsConflict, assign_role

    command.upgrade(Config("alembic.ini"), "head")
    with make_client(tmp_path, settings=postgres_settings) as client:
        factory = client.app.state.session_factory
        issuer = client.app.state.container.settings.cognito_issuer
        principals = []
        for subject in ("first-admin", "second-admin"):
            client.sign_in(subject, f"{subject}@example.com")
            assign_role(factory, issuer, subject, "admin", actor="operator:test",
                        reason="PostgreSQL admin test", apply=True)
            assert client.get("/api/v1/models").status_code == 401
            principals.append(client.sign_in(subject, f"{subject}@example.com"))
        service = client.app.state.container.admin_service
        barrier = Barrier(2)

        def save(principal):
            barrier.wait(timeout=10)
            try:
                service.save_models(principal, "test-model", ["test-model"], 0, "Concurrent update")
                return "saved"
            except SettingsConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(save, principals)) == ["conflict", "saved"]
        assert service.model_settings().version == 1
        assert len([e for e in service.recent_audit() if e.action == "models.change"]) == 1
        assert client.get("/admin").status_code == 200
        assign_role(factory, issuer, "second-admin", "user", actor="operator:test",
                    reason="Revoke test admin", apply=True)
        assert client.get("/api/v1/models").status_code == 401
        assert client.sign_in("second-admin", "second-admin@example.com").role == "user"
        assert client.get("/admin").status_code == 403
    with make_client(tmp_path, settings=postgres_settings) as client:
        assert client.app.state.container.admin_service.model_settings().version == 1


def test_invitation_atomicity_and_concurrent_redemption(postgres_settings, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import event, func, select
    from sqlalchemy.exc import OperationalError
    from app.repositories.database import AdminAuditRecord, InvitationRecord, UserRecord
    from app.services.auth_service import AuthenticationError
    from tests.integration.test_admin import promote
    from tests.integration.test_invitations import identity

    command.upgrade(Config("alembic.ini"), "head")
    with make_client(tmp_path, settings=postgres_settings) as client:
        admin = promote(client)
        auth = client.app.state.container.auth_service
        code = auth.invitations.generate(admin)
        auth.invitations.save_policy(admin, True, 0)
        with auth.factory() as db:
            invitation_id = db.scalar(select(InvitationRecord.id))
        engine = auth.factory.kw["bind"]
        def fail_session(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO auth_sessions"):
                raise OperationalError(statement, {}, Exception("Injected session failure"))
        event.listen(engine, "before_cursor_execute", fail_session)
        try:
            with pytest.raises(OperationalError):
                auth.establish_session(identity(client, "rollback-user"), invitation_id=invitation_id)
        finally:
            event.remove(engine, "before_cursor_execute", fail_session)
        with auth.factory() as db:
            assert db.get(InvitationRecord, invitation_id).used_at is None
            assert db.scalar(select(UserRecord).where(UserRecord.subject == "rollback-user")) is None
        auth.begin_login("signup", code)
        barrier = Barrier(2)
        def redeem(subject):
            barrier.wait(timeout=10)
            try:
                auth.establish_session(identity(client, subject), invitation_id=invitation_id)
                return "saved"
            except AuthenticationError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(redeem, ("first-invitee", "second-invitee"))) == ["rejected", "saved"]
        with auth.factory() as db:
            assert db.scalar(select(func.count()).select_from(UserRecord).where(
                UserRecord.subject.in_(["first-invitee", "second-invitee"]))) == 1
            assert db.scalar(select(func.count()).select_from(AdminAuditRecord).where(
                AdminAuditRecord.action == "invitation.redeem")) == 1
        # Throttle counters are shared across PostgreSQL sessions as well.
        auth.invitations.check_rate_limit("postgres-test")
        auth.invitations.check_rate_limit("postgres-test")
