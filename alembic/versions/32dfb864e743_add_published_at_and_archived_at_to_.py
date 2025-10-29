"""add published_at and archived_at to entries

Revision ID: 32dfb864e743
Revises: 60fe5b4ef62a
Create Date: 2025-10-29 20:39:37.312683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32dfb864e743'
down_revision: Union[str, Sequence[str], None] = '60fe5b4ef62a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    op.add_column("entries", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("entries", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_entries_published_at", "entries", ["published_at"])
    op.create_index("ix_entries_archived_at", "entries", ["archived_at"])

def downgrade():
    op.drop_index("ix_entries_archived_at", table_name="entries")
    op.drop_index("ix_entries_published_at", table_name="entries")
    op.drop_column("entries", "archived_at")
    op.drop_column("entries", "published_at")
