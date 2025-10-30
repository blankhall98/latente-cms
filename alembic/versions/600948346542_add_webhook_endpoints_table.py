"""add webhook_endpoints table

Revision ID: 600948346542
Revises: 396c10e817e6
Create Date: 2025-10-30 22:14:40.908120

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '600948346542'
down_revision: Union[str, Sequence[str], None] = '396c10e817e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("event_filter", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Índice idempotente (evita DuplicateTable si ya existe)
    op.create_index(
        "ix_webhook_endpoints_tenant_id",
        "webhook_endpoints",
        ["tenant_id"],
        unique=False,
        if_not_exists=True,  # <-- clave para no fallar si ya existe
    )


def downgrade() -> None:
    # Borrado idempotente por si no existe
    op.drop_index(
        "ix_webhook_endpoints_tenant_id",
        table_name="webhook_endpoints",
        if_exists=True,  # <-- evita fallar si no existe
    )
    op.drop_table("webhook_endpoints")
