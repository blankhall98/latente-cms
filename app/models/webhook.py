# app/models/webhook.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)

    # URL de destino del webhook
    url: Mapped[str] = mapped_column(String(512), nullable=False)

    # Secreto para firmar (HMAC) los webhooks
    secret: Mapped[str] = mapped_column(String(255), nullable=False)

    # Habilitado/Deshabilitado
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Filtro simple de eventos como CSV (ej. "content.published,content.archived")
    # Si prefieres JSONB, cambia a postgresql.JSONB y ajusta migración.
    event_filter: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Timestamp de creación
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

