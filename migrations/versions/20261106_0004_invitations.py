"""Invitation-only registration and shared signup throttling."""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0004"
down_revision = "20261105_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("suffix", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True, unique=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint("used_at IS NULL OR deactivated_at IS NULL", name="ck_invitation_state"),
    )
    with op.batch_alter_table("login_attempts") as batch:
        batch.add_column(sa.Column("invitation_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_login_attempt_invitation", "invitations", ["invitation_id"], ["id"])
    op.create_table(
        "signup_throttle",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signup_throttle_expires_at", "signup_throttle", ["expires_at"])


def downgrade():
    op.drop_index("ix_signup_throttle_expires_at", table_name="signup_throttle")
    op.drop_table("signup_throttle")
    with op.batch_alter_table("login_attempts") as batch:
        batch.drop_constraint("fk_login_attempt_invitation", type_="foreignkey")
        batch.drop_column("invitation_id")
    op.drop_table("invitations")
