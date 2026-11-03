"""Add users, sessions, login attempts, and private analysis ownership.

Existing analyses intentionally remain unassigned and inaccessible to app users.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261102_0002"
down_revision = "20261005_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_identity"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encrypted_tokens", sa.Text(), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "login_attempts",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("verifier", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_attempts_expires_at", "login_attempts", ["expires_at"])
    # Batch mode supports SQLite as well as PostgreSQL, including the foreign key.
    with op.batch_alter_table("analyses") as batch:
        batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_analyses_user_id", "users", ["user_id"], ["id"])
        batch.create_index("ix_analyses_user_id", ["user_id"])


def downgrade():
    with op.batch_alter_table("analyses") as batch:
        batch.drop_index("ix_analyses_user_id")
        batch.drop_constraint("fk_analyses_user_id", type_="foreignkey")
        batch.drop_column("user_id")
    op.drop_table("login_attempts")
    op.drop_table("auth_sessions")
    op.drop_table("users")
