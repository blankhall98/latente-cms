# app/api/v1/endpoints/content.py
# ⟶ Añadimos GET /sections/{id}/schema-active y GET /sections/{id}/registry,
#    y el PATCH para activar versiones con compat-check.
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.db.session import get_db
from app.schemas.content import (
    SectionCreate, SectionUpdate, SectionOut,
    SectionSchemaCreate, SectionSchemaUpdate, SectionSchemaOut,
    EntryCreate, EntryUpdate, EntryOut
)
from app.models.content import SectionSchema, Entry
from app.services.content_service import (
    create_section, add_schema_version, set_active_schema,
    create_entry, update_entry, list_entries
)
from app.services.registry_service import (
    get_registry_for_section,
    get_active_schema as rs_get_active_schema,
    can_activate_version
)

router = APIRouter()

# Hook RBAC (placeholder)
def require_permission(permission: str):
    def _dep():
        return True
    return _dep


# ----- Sections -----
@router.post("/sections", response_model=SectionOut, dependencies=[Depends(require_permission("content:write"))])
def create_section_endpoint(payload: SectionCreate, db: Session = Depends(get_db)):
    section = create_section(
        db,
        tenant_id=payload.tenant_id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
    )
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
        schema=payload.schema,     # el body viene como "schema"
        title=payload.title,
        is_active=payload.is_active or False,
    )
    db.commit()
    db.refresh(ss)
    return ss


@router.patch("/section-schemas/{tenant_id}/{section_id}/{version}", response_model=SectionSchemaOut, dependencies=[Depends(require_permission("content:write"))])
def update_schema_endpoint(tenant_id: int, section_id: int, version: int, patch: SectionSchemaUpdate, db: Session = Depends(get_db)):
    # si se solicita activar: correr compat-check
    if patch.is_active is True:
        ok, errs = can_activate_version(db, tenant_id=tenant_id, section_id=section_id, target_version=version)
        if not ok:
            raise HTTPException(status_code=400, detail={"message": "Activation blocked by registry policy", "errors": errs})
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

    # si no es activación, permitir cambiar solo el título
    ss = db.scalar(
        select(SectionSchema).where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.version == version,
            )
        )
    )
    if not ss:
        raise HTTPException(status_code=404, detail="Schema not found")
    if patch.title is not None:
        ss.title = patch.title
    db.commit()
    db.refresh(ss)
    return ss


# ----- Nuevos endpoints de lectura (útiles para la UI) -----
@router.get("/sections/{section_id}/schema-active", dependencies=[Depends(require_permission("content:read"))])
def get_active_schema_endpoint(section_id: int, tenant_id: int = Query(...), db: Session = Depends(get_db)):
    ss = rs_get_active_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        return {"active": None, "message": "No active schema for this section."}
    return {
        "active": {
            "version": ss.version,
            "title": ss.title,
            "is_active": getattr(ss, "is_active", False),
            "created_at": ss.created_at,
        }
    }

@router.get("/sections/{section_id}/registry", dependencies=[Depends(require_permission("content:read"))])
def get_registry_endpoint(section_id: int, tenant_id: int | None = Query(None), db: Session = Depends(get_db)):
    reg = get_registry_for_section(db, section_id=section_id, tenant_id=tenant_id)
    if not reg:
        return {"registry": None, "message": "No registry declared for this section key."}
    return {"registry": reg}


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
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    entries = list_entries(
        db,
        tenant_id=tenant_id,
        section_id=section_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return entries
