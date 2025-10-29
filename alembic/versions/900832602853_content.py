"""content

Revision ID: 900832602853
Revises: 3c9e2f07eb56
Create Date: 2025-10-29 18:11:46.945901

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '900832602853'
down_revision: Union[str, Sequence[str], None] = '3c9e2f07eb56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    entry_status = sa.Enum("draft", "published", "archived", name="entry_status", native_enum=False)
    entry_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "sections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_section_tenant_key"),
    )
    op.create_index("ix_sections_tenant_key", "sections", ["tenant_id", "key"])

    op.create_table(
        "section_schemas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=True),
        sa.Column("schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "section_id", "version", name="uq_section_schema_version"),
    )
    op.create_index(
        "ix_section_schemas_tenant_section_version",
        "section_schemas",
        ["tenant_id", "section_id", "version"]
    )

    op.create_table(
        "entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", entry_status, nullable=False, server_default="draft"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "section_id", "slug", name="uq_entry_slug_per_section", deferrable=True, initially="DEFERRED"),
    )
    op.create_index("ix_entries_tenant_section_status", "entries", ["tenant_id", "section_id", "status"])
    op.create_index("ix_entries_data_gin", "entries", ["data"], postgresql_using="gin")


def downgrade():
    op.drop_index("ix_entries_data_gin", table_name="entries")
    op.drop_index("ix_entries_tenant_section_status", table_name="entries")
    op.drop_table("entries")

    op.drop_index("ix_section_schemas_tenant_section_version", table_name="section_schemas")
    op.drop_table("section_schemas")

    op.drop_index("ix_sections_tenant_key", table_name="sections")
    op.drop_table("sections")

    entry_status = sa.Enum(name="entry_status")
    entry_status.drop(op.get_bind(), checkfirst=True)
