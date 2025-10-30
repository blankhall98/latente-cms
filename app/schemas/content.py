# app/schemas/content.py
# Pydantic — requests/responses para Sections, Schemas y Entries
from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal, Dict, Any

from pydantic import BaseModel, Field, ConfigDict

EntryStatus = Literal["draft", "published", "archived"]

# ---------- Section ----------
class SectionBase(BaseModel):
    key: str = Field(..., max_length=64)
    name: str = Field(..., max_length=128)
    description: Optional[str] = Field(None, max_length=512)

class SectionCreate(SectionBase):
    tenant_id: int

class SectionUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=512)

class SectionOut(SectionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int
    created_at: datetime
    updated_at: datetime


# ---------- SectionSchema ----------
class SectionSchemaBase(BaseModel):
    version: int = Field(..., ge=1)
    title: Optional[str] = Field(None, max_length=128)
    # Evita warning de Pydantic por 'schema' usando alias:
    json_schema: dict = Field(..., alias="schema")
    is_active: bool = False

    model_config = ConfigDict(populate_by_name=True)  # permite usar json_schema OR alias 'schema'

class SectionSchemaCreate(SectionSchemaBase):
    tenant_id: int
    section_id: int

class SectionSchemaUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=128)
    is_active: Optional[bool] = None

class SectionSchemaOut(SectionSchemaBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int
    section_id: int
    created_at: datetime


# ---------- Entry ----------
class EntryBase(BaseModel):
    slug: Optional[str] = Field(None, max_length=128)
    schema_version: int = Field(..., ge=1)
    status: EntryStatus = "draft"
    data: dict

class EntryCreate(EntryBase):
    tenant_id: int
    section_id: int

class EntryUpdate(BaseModel):
    tenant_id: int                               # <-- necesario para el test
    slug: Optional[str] = Field(None, max_length=128)
    status: Optional[EntryStatus] = None
    data: Optional[Dict[str, Any]] = None
    schema_version: Optional[int] = Field(None, ge=1)

    # Evita 422 por campos adicionales que no uses
    model_config = ConfigDict(extra="ignore")

class EntryOut(EntryBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int
    section_id: int
    published_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

