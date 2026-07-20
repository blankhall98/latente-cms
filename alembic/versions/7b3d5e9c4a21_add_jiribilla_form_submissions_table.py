"""add jiribilla_form_submissions table

Revision ID: 7b3d5e9c4a21
Revises: 2f7f8c1a9d3a
Create Date: 2026-07-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "7b3d5e9c4a21"
down_revision: Union[str, Sequence[str], None] = "2f7f8c1a9d3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jiribilla_form_submissions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("form_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("data", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("cv_url", sa.String(length=1024), nullable=True),
        sa.Column("email_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_jiribilla_form_submissions_tenant_id",
        "jiribilla_form_submissions",
        ["tenant_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_jiribilla_form_submissions_tenant_form_created",
        "jiribilla_form_submissions",
        ["tenant_id", "form_type", "created_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jiribilla_form_submissions_tenant_form_created",
        table_name="jiribilla_form_submissions",
        if_exists=True,
    )
    op.drop_index(
        "ix_jiribilla_form_submissions_tenant_id",
        table_name="jiribilla_form_submissions",
        if_exists=True,
    )
    op.drop_table("jiribilla_form_submissions")
