# app/services/content_service.py
# Lógica de negocio + validación JSON Schema + búsquedas JSONB (Paso 9)
from __future__ import annotations
from typing import Optional, Sequence

from sqlalchemy import select, and_, func, update
from sqlalchemy.orm import Session

from jsonschema import Draft202012Validator

from app.models.content import Section, SectionSchema, Entry
from app.schemas.content import EntryCreate, EntryUpdate

# -------- Helpers JSONB (consultas en data) --------
def _jsonb_field_ilike(path: list[str], value: str):
    """
    ILIKE sobre jsonb_extract_path_text(data, *path)
    Ej: path=['hero','title']  -> data->'hero'->>'title' ILIKE %value%
    """
    return func.jsonb_extract_path_text(Entry.data, *path).ilike(f"%{value}%")

def _jsonb_field_eq(path: list[str], value: str):
    return func.jsonb_extract_path_text(Entry.data, *path) == value


# -------- Sections --------
def create_section(db: Session, *, tenant_id: int, key: str, name: str, description: Optional[str] = None) -> Section:
    exists = db.scalar(select(Section).where(and_(Section.tenant_id == tenant_id, Section.key == key)))
    if exists:
        return exists  # idempotente
    section = Section(tenant_id=tenant_id, key=key, name=name, description=description)
    db.add(section)
    db.flush()
    return section


# -------- Section Schemas --------
def add_schema_version(
    db: Session,
    *,
    tenant_id: int,
    section_id: int,
    version: int,
    schema: dict,
    title: Optional[str] = None,
    is_active: bool = False,
) -> SectionSchema:
    """
    Crea (o devuelve) una versión de SectionSchema. SIEMPRE inserta como inactiva
    y si is_active=True, activa la versión en un segundo paso atómico (set_active_schema).
    Esto evita violar el índice único parcial "solo 1 activo por sección".
    """
    exists = db.scalar(
        select(SectionSchema).where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.version == version,
            )
        )
    )
    if exists:
        # Idempotente; opcionalmente actualiza título y activa
        if title is not None:
            exists.title = title
        db.flush()
        if is_active and not exists.is_active:
            set_active_schema(db, tenant_id=tenant_id, section_id=section_id, version=version)
        return exists

    # Fuerza inserción como INACTIVA para no chocar con el unique parcial
    ss = SectionSchema(
        tenant_id=tenant_id,
        section_id=section_id,
        version=version,
        schema=schema,
        title=title,
        is_active=False,  # ← clave
    )
    db.add(ss)
    db.flush()

    # Activación segura (dos pasos)
    if is_active:
        set_active_schema(db, tenant_id=tenant_id, section_id=section_id, version=version)

    return ss


def set_active_schema(db: Session, *, tenant_id: int, section_id: int, version: int) -> SectionSchema:
    """
    Marca una versión como activa y desactiva cualquier otra (máximo 1 activa por sección+tenant).
    Requiere índice único parcial a nivel DB (creado en la migración del Paso 9).
    """
    # Desactivar las activas previas
    db.execute(
        update(SectionSchema)
        .where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.is_active == True,  # noqa: E712
            )
        )
        .values(is_active=False)
    )
    db.flush()

    # Activar target
    target = db.scalar(
        select(SectionSchema).where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.version == version,
            )
        )
    )
    if not target:
        raise ValueError("SectionSchema version not found.")
    target.is_active = True
    db.flush()
    return target


def get_effective_schema(db: Session, *, tenant_id: int, section_id: int) -> SectionSchema | None:
    """
    Devuelve el esquema activo; si no existe, devuelve el de mayor versión.
    """
    active = db.scalar(
        select(SectionSchema)
        .where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.is_active == True,  # noqa: E712
            )
        )
        .limit(1)
    )
    if active:
        return active

    latest = db.scalar(
        select(SectionSchema)
        .where(and_(SectionSchema.tenant_id == tenant_id, SectionSchema.section_id == section_id))
        .order_by(SectionSchema.version.desc())
        .limit(1)
    )
    return latest


def get_section_schema(db: Session, *, tenant_id: int, section_id: int, version: int) -> SectionSchema | None:
    return db.scalar(
        select(SectionSchema).where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.version == version,
            )
        )
    )


# -------- Entries --------
def _validate_entry_against_schema(*, data: dict, schema: dict) -> None:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        e = errors[0]
        path = ".".join([str(p) for p in e.path])
        msg = f"JSON Schema validation error at '{path}': {e.message}"
        raise ValueError(msg)


def create_entry(db: Session, payload: EntryCreate) -> Entry:
    # valida existencia de schema
    ss = get_section_schema(
        db,
        tenant_id=payload.tenant_id,
        section_id=payload.section_id,
        version=payload.schema_version,
    )
    if not ss:
        raise ValueError("SectionSchema not found for the given section_id and schema_version.")

    # valida data
    _validate_entry_against_schema(data=payload.data, schema=ss.schema)

    entry = Entry(
        tenant_id=payload.tenant_id,
        section_id=payload.section_id,
        slug=payload.slug,
        schema_version=payload.schema_version,
        status=payload.status,
        data=payload.data,
    )
    db.add(entry)
    db.flush()
    return entry


def update_entry(db: Session, entry_id: int, tenant_id: int, patch: EntryUpdate) -> Entry:
    entry = db.get(Entry, entry_id)
    if not entry or entry.tenant_id != tenant_id:
        raise ValueError("Entry not found.")

    new_version = patch.schema_version if patch.schema_version is not None else entry.schema_version
    new_data = patch.data if patch.data is not None else entry.data

    if (patch.schema_version is not None) or (patch.data is not None):
        ss = get_section_schema(db, tenant_id=tenant_id, section_id=entry.section_id, version=new_version)
        if not ss:
            raise ValueError("SectionSchema not found for the new schema_version.")
        _validate_entry_against_schema(data=new_data, schema=ss.schema)
        entry.schema_version = new_version
        entry.data = new_data

    if patch.slug is not None:
        entry.slug = patch.slug
    if patch.status is not None:
        entry.status = patch.status

    db.flush()
    return entry


def list_entries(
    db: Session,
    *,
    tenant_id: int,
    section_id: Optional[int] = None,
    status: Optional[str] = None,
    q_ilike: list[tuple[list[str], str]] | None = None,
    q_eq: list[tuple[list[str], str]] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Entry]:
    stmt = select(Entry).where(Entry.tenant_id == tenant_id)
    if section_id:
        stmt = stmt.where(Entry.section_id == section_id)
    if status:
        stmt = stmt.where(Entry.status == status)

    # Filtros JSONB
    if q_ilike:
        for path, value in q_ilike:
            stmt = stmt.where(_jsonb_field_ilike(path, value))
    if q_eq:
        for path, value in q_eq:
            stmt = stmt.where(_jsonb_field_eq(path, value))

    stmt = stmt.order_by(Entry.created_at.desc()).limit(limit).offset(offset)
    return db.scalars(stmt).all()

