# app/api/v1/endpoints/content.py
# Rutas FastAPI — CRUD + búsqueda JSONB + activar schema (Paso 9)
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.db.session import get_db
from app.schemas.content import (
    SectionCreate, SectionOut,
    SectionSchemaCreate, SectionSchemaUpdate, SectionSchemaOut,
    EntryCreate, EntryUpdate, EntryOut
)
from app.models.content import SectionSchema
from app.services.content_service import (
    create_section, add_schema_version, set_active_schema,
    create_entry, update_entry, list_entries
)

router = APIRouter()

# ---- Hook RBAC (placeholder) ----
def require_permission(permission: str):
    def _dep():
        # TODO: integrar con JWT + UserTenant + RolePermission
        return True
    return _dep


# ----- Sections -----
@router.post("/sections", response_model=SectionOut, dependencies=[Depends(require_permission("content:write"))])
def create_section_endpoint(payload: SectionCreate, db: Session = Depends(get_db)):
    section = create_section(db, tenant_id=payload.tenant_id, key=payload.key, name=payload.name, description=payload.description)
    db.commit()
    db.refresh(section)
    return section


# ----- Section Schemas -----
@router.post("/section-schemas", response_model=SectionSchemaOut, dependencies=[Depends(require_permission("content:write"))])
def add_schema_version_endpoint(payload: SectionSchemaCreate, db: Session = Depends(get_db)):
    ss = add_schema_version(
        db,
        tenant_id=payload.tenant_id,
        section_id=payload.section_id,
        version=payload.version,
        schema=payload.json_schema,  # usa alias para evitar warning de Pydantic
        title=payload.title,
        is_active=payload.is_active,
    )
    db.commit()
    db.refresh(ss)
    return ss


@router.patch("/section-schemas/{tenant_id}/{section_id}/{version}", response_model=SectionSchemaOut, dependencies=[Depends(require_permission("content:write"))])
def update_schema_endpoint(tenant_id: int, section_id: int, version: int, patch: SectionSchemaUpdate, db: Session = Depends(get_db)):
    # Activar una versión
    if patch.is_active is True:
        try:
            ss = set_active_schema(db, tenant_id=tenant_id, section_id=section_id, version=version)
            if patch.title is not None:
                ss.title = patch.title
            db.commit()
            db.refresh(ss)
            return ss
        except ValueError as e:
            db.rollback()
            raise HTTPException(status_code=404, detail=str(e))
    # Actualizar solo título (no tocamos is_active=False desde aquí para evitar confusiones)
    ss = db.scalar(
        select(SectionSchema).where(
            and_(SectionSchema.tenant_id == tenant_id, SectionSchema.section_id == section_id, SectionSchema.version == version)
        )
    )
    if not ss:
        raise HTTPException(status_code=404, detail="Schema not found")
    if patch.title is not None:
        ss.title = patch.title
    db.commit()
    db.refresh(ss)
    return ss


# ----- Entries -----
@router.post("/entries", response_model=EntryOut, dependencies=[Depends(require_permission("content:write"))])
def create_entry_endpoint(payload: EntryCreate, db: Session = Depends(get_db)):
    try:
        entry = create_entry(db, payload)
        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/entries/{entry_id}", response_model=EntryOut, dependencies=[Depends(require_permission("content:write"))])
def update_entry_endpoint(entry_id: int, tenant_id: int, patch: EntryUpdate, db: Session = Depends(get_db)):
    try:
        entry = update_entry(db, entry_id, tenant_id, patch)
        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/entries", response_model=list[EntryOut], dependencies=[Depends(require_permission("content:read"))])
def list_entries_endpoint(
    tenant_id: int = Query(...),
    section_id: int | None = Query(None),
    status: str | None = Query(None),
    # Filtros JSONB (puedes repetir el parámetro)
    # q_ilike=hero.title~=Bienvenido
    # q_eq=seo.title==Home
    q_ilike: list[str] = Query(default=[]),
    q_eq: list[str] = Query(default=[]),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    def parse_ilike_item(item: str) -> tuple[list[str], str]:
        if "~=" not in item:
            raise HTTPException(status_code=400, detail=f"Invalid q_ilike item: {item}")
        left, value = item.split("~=", 1)
        path = [p.strip() for p in left.split(".") if p.strip()]
        if not path or value == "":
            raise HTTPException(status_code=400, detail=f"Invalid q_ilike item: {item}")
        return (path, value)

    def parse_eq_item(item: str) -> tuple[list[str], str]:
        if "==" not in item:
            raise HTTPException(status_code=400, detail=f"Invalid q_eq item: {item}")
        left, value = item.split("==", 1)
        path = [p.strip() for p in left.split(".") if p.strip()]
        if not path:
            raise HTTPException(status_code=400, detail=f"Invalid q_eq item: {item}")
        return (path, value)

    _q_ilike = [parse_ilike_item(x) for x in q_ilike]
    _q_eq = [parse_eq_item(x) for x in q_eq]

    entries = list_entries(
        db,
        tenant_id=tenant_id,
        section_id=section_id,
        status=status,
        q_ilike=_q_ilike,
        q_eq=_q_eq,
        limit=limit,
        offset=offset,
    )
    return entries
