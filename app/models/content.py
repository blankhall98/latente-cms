# app/models/content.py

from __future__ import annotations
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Integer, ForeignKey, DateTime, Enum, UniqueConstraint, Index, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

EntryStatus = Enum(
    "draft", "published", "archived",
    name="entry_status",
    create_constraint=True,
    validate_strings=True,
    native_enum=False,
)

class Section(Base):
    __tablename__ = "sections"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    schemas: Mapped[list["SectionSchema"]] = relationship("SectionSchema", back_populates="section", cascade="all, delete-orphan")
    entries: Mapped[list["Entry"]] = relationship("Entry", back_populates="section", cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_section_tenant_key"),
        Index("ix_sections_tenant_key", "tenant_id", "key"),
    )

class SectionSchema(Base):
    __tablename__ = "section_schemas"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    schema: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(default=False)
    section: Mapped["Section"] = relationship("Section", back_populates="schemas")
    __table_args__ = (
        UniqueConstraint("tenant_id", "section_id", "version", name="uq_section_schema_version"),
        Index("ix_section_schemas_tenant_section_version", "tenant_id", "section_id", "version"),
    )

class Entry(Base):
    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), index=True)
    slug: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(EntryStatus, default="draft")
    data: Mapped[dict] = mapped_column(JSONB)

    # NUEVO — auditoría de publicación
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    section: Mapped["Section"] = relationship("Section", back_populates="entries")

    __table_args__ = (
        UniqueConstraint("tenant_id", "section_id", "slug", name="uq_entry_slug_per_section", deferrable=True, initially="DEFERRED"),
        Index("ix_entries_tenant_section_status", "tenant_id", "section_id", "status"),
        Index("ix_entries_data_gin", data, postgresql_using="gin"),
        Index("ix_entries_published_at", "published_at"),
        Index("ix_entries_archived_at", "archived_at"),
    )

