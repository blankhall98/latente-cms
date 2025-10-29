"""Paso 9: section_schemas.is_active + unique active index

Revision ID: 60fe5b4ef62a
Revises: 900832602853
Create Date: 2025-10-29 18:43:39.182972

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '60fe5b4ef62a'
down_revision: Union[str, Sequence[str], None] = '900832602853'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    # 1) Agregar columna is_active (por defecto FALSE)
    op.add_column(
        "section_schemas",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )

    # 2) Índice único parcial: solo una fila activa por tenant+section
    #    Nota: esto es específico de PostgreSQL
    op.create_index(
        "uq_section_schema_active_one_per_section",   # nombre del índice
        "section_schemas",                            # tabla
        ["tenant_id", "section_id"],                  # columnas evaluadas
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
    )


def downgrade():
    # Revertir en orden inverso
    op.drop_index("uq_section_schema_active_one_per_section", table_name="section_schemas")
    op.drop_column("section_schemas", "is_active")
