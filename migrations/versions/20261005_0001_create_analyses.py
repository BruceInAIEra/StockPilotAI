"""Create analyses table.

Revision ID: 20261005_0001
Revises:
Create Date: 2026-10-05
"""

from alembic import op
import sqlalchemy as sa

revision = "20261005_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(length=15), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("horizon", sa.String(length=30), nullable=False),
        sa.Column("owns_stock", sa.Boolean(), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("market_data_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyses_action", "analyses", ["action"])
    op.create_index("ix_analyses_created_at", "analyses", ["created_at"])
    op.create_index("ix_analyses_status", "analyses", ["status"])
    op.create_index("ix_analyses_symbol", "analyses", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_analyses_symbol", table_name="analyses")
    op.drop_index("ix_analyses_status", table_name="analyses")
    op.drop_index("ix_analyses_created_at", table_name="analyses")
    op.drop_index("ix_analyses_action", table_name="analyses")
    op.drop_table("analyses")

