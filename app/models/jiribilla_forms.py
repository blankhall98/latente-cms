from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

FORM_TYPE_EVENTOS = "eventos_privados"
FORM_TYPE_BOLSA = "bolsa_trabajo"


class JiribillaFormSubmission(Base):
    __tablename__ = "jiribilla_form_submissions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    form_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str] = mapped_column(String(64), nullable=False)
    # Form-specific fields (Spanish keys) rendered verbatim in email and inbox.
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cv_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    email_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_jiribilla_form_submissions_tenant_form_created",
            "tenant_id",
            "form_type",
            "created_at",
        ),
    )
