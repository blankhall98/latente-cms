"""EntryVershion snapshots

Revision ID: 396c10e817e6
Revises: 1424a9e13ff6
Create Date: 2025-10-30 20:46:20.390844

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '396c10e817e6'
down_revision: Union[str, Sequence[str], None] = '1424a9e13ff6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    op.create_table(
        "entry_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_id", sa.BigInteger(), sa.ForeignKey("entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.BigInteger(), sa.ForeignKey("sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_idx", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_entry_versions_tenant_entry_idx", "entry_versions", ["tenant_id", "entry_id", "version_idx"])
    op.create_unique_constraint("uq_entry_versions_per_entry", "entry_versions", ["tenant_id", "entry_id", "version_idx"])

def downgrade():
    op.drop_constraint("uq_entry_versions_per_entry", "entry_versions", type_="unique")
    op.drop_index("ix_entry_versions_tenant_entry_idx", table_name="entry_versions")
    op.drop_table("entry_versions")