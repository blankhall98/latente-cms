# app/schemas/delivery.py
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field

class DeliveryEntryOut(BaseModel):
    id: int
    tenant_id: int
    section_id: int
    slug: str | None = None
    status: str = Field(description="En delivery siempre será 'published'")
    schema_version: int
    data: dict
    updated_at: datetime | None = None
    published_at: datetime | None = None

class DeliveryEntryListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DeliveryEntryOut]
