"""add owa_popup_submissions table

Revision ID: 2f7f8c1a9d3a
Revises: 600948346542
Create Date: 2026-02-17 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2f7f8c1a9d3a"
down_revision: Union[str, Sequence[str], None] = "600948346542"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "owa_popup_submissions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("gender", sa.String(length=64), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_owa_popup_submissions_tenant_created",
        "owa_popup_submissions",
        ["tenant_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_owa_popup_submissions_tenant_gender",
        "owa_popup_submissions",
        ["tenant_id", "gender"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_owa_popup_submissions_tenant_gender",
        table_name="owa_popup_submissions",
        if_exists=True,
    )
    op.drop_index(
        "ix_owa_popup_submissions_tenant_created",
        table_name="owa_popup_submissions",
        if_exists=True,
    )
    op.drop_table("owa_popup_submissions")
