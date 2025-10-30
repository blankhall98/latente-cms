"""content audit log

Revision ID: 1424a9e13ff6
Revises: 32dfb864e743
Create Date: 2025-10-30 17:07:04.183054

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1424a9e13ff6'
down_revision: Union[str, Sequence[str], None] = '32dfb864e743'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    bind = op.get_bind()

    # (A) Asegurar que el TYPE exista (una sola vez)
    #    Usamos postgresql.ENUM con create_type=True y checkfirst=True
    contentaction_create = postgresql.ENUM(
        "create", "update", "publish", "unpublish", "archive", "restore",
        name="contentaction"
    )
    contentaction_create.create(bind, checkfirst=True)

    # (B) Definir una instancia del mismo TYPE pero con create_type=False para la columna
    contentaction = postgresql.ENUM(name="contentaction", create_type=False)

    op.create_table(
        "content_audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_id", sa.BigInteger(), sa.ForeignKey("entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.BigInteger(), sa.ForeignKey("sections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", contentaction, nullable=False),  # <-- reusa el TYPE existente
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_index(
        "ix_content_audit_logs_tenant_entry_created_desc",
        "content_audit_logs",
        ["tenant_id", "entry_id", "created_at"],
        unique=False
    )
    op.create_index(
        "ix_content_audit_logs_action",
        "content_audit_logs",
        ["action"],
        unique=False
    )
    op.create_index(
        "ix_content_audit_logs_details_gin",
        "content_audit_logs",
        ["details"],
        unique=False,
        postgresql_using="gin"
    )


def downgrade():
    # El orden importa: primero borra índices y tabla, luego el TYPE
    op.drop_index("ix_content_audit_logs_details_gin", table_name="content_audit_logs")
    op.drop_index("ix_content_audit_logs_action", table_name="content_audit_logs")
    op.drop_index("ix_content_audit_logs_tenant_entry_created_desc", table_name="content_audit_logs")
    op.drop_table("content_audit_logs")

    # Intentar borrar el TYPE (solo si nadie más lo usa)
    bind = op.get_bind()
    contentaction = postgresql.ENUM(name="contentaction")
    contentaction.drop(bind, checkfirst=True)