import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select

from app.core.config import get_settings
from app.manage_accounts import assign_legacy
from app.repositories.database import AnalysisRecord
from tests.integration.test_app import make_client


def test_migration_preserves_unassigned_history_and_explicit_claim(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20261005_0001")
        with sqlite3.connect(path) as db:
            db.execute("""INSERT INTO analyses (id, created_at, symbol, company_name, horizon, owns_stock,
                       model_name, status, request_json) VALUES
                       ('legacy-report', '2026-10-01', 'AAPL', 'Apple', 'long_term', 0, 'test-model', 'running', '{}')""")
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        command.check(config)
        with make_client(tmp_path) as client:
            assert client.get("/api/v1/analyses").json() == []
            assert client.get("/api/v1/analyses/legacy-report").status_code == 404
            factory = client.app.state.session_factory
            principal = client.sign_in()
            assert assign_legacy(factory, principal.id) == 1
            assert client.get("/api/v1/analyses").json() == []
            assert assign_legacy(factory, principal.id, apply=True) == 1
            assert len(client.get("/api/v1/analyses").json()) == 1
            assert assign_legacy(factory, principal.id, apply=True) == 0
        command.downgrade(config, "20261005_0001")
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT symbol FROM analyses WHERE id='legacy-report'").fetchone() == ("AAPL",)
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()


def test_admin_migration_defaults_existing_users_and_preserves_sessions(tmp_path, monkeypatch):
    path = tmp_path / "existing-users.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20261102_0002")
        with sqlite3.connect(path) as db:
            db.execute("""INSERT INTO users (id, issuer, subject, email, created_at, disabled)
                       VALUES ('existing-user', 'issuer', 'subject', 'existing@example.com', '2026-11-01', 0)""")
            db.execute("""INSERT INTO auth_sessions
                       (token_hash, user_id, csrf_token, expires_at, encrypted_tokens)
                       VALUES ('hash', 'existing-user', 'csrf', '2026-11-06', 'encrypted')""")
        command.upgrade(config, "head")
        command.check(config)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT role FROM users WHERE id='existing-user'").fetchone() == ("user",)
            assert db.execute("SELECT user_id FROM auth_sessions").fetchone() == ("existing-user",)
        command.downgrade(config, "20261102_0002")
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT subject FROM users").fetchone() == ("subject",)
            assert db.execute("SELECT user_id FROM auth_sessions").fetchone() == ("existing-user",)
    finally:
        get_settings.cache_clear()
