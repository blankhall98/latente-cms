# app/models/audit.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

from sqlalchemy import (
    BigInteger, String, Enum as SAEnum, DateTime, ForeignKey, JSON, Index, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ContentAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    PUBLISH = "publish"
    UNPUBLISH = "unpublish"
    ARCHIVE = "archive"
    RESTORE = "restore"  # reservado para el Paso 17


class ContentAuditLog(Base):
    __tablename__ = "content_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sections.id", ondelete="SET NULL"), nullable=True
    )

    action: Mapped[ContentAction] = mapped_column(
        SAEnum(
            ContentAction,
            name="contentaction",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],  # <— usa los values ("publish")
            native_enum=True,  # ok si ya tienes el tipo en PG, manténlo
            validate_strings=True,
        ),
        nullable=False,
    )

    # Usuario que originó la acción (puede ser None para procesos del sistema / API key)
    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Metadatos/detalles del evento: claves cambiadas, status previo/nuevo, diffs, IP/UA, etc.
    details: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    # Relaciones opcionales (por conveniencia)
    entry = relationship("Entry", backref="audit_logs")
    # tenant, section, user relaciones existen pero no son necesarias aquí

    __table_args__ = (
        Index(
            "ix_content_audit_logs_tenant_entry_created_desc",
            "tenant_id", "entry_id", "created_at",
            postgresql_using="btree"
        ),
        Index("ix_content_audit_logs_action", "action", postgresql_using="btree"),
        # Index GIN en details para consultas por campo (PostgreSQL)
        Index("ix_content_audit_logs_details_gin", "details", postgresql_using="gin"),
    )
