# app/api/v1/endpoints/schemas.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.content import SectionSchema
from app.deps.auth import require_permission  # unified auth deps

# Use service-layer helpers to avoid circular imports
from app.services.registry_service import get_active_schema as rs_get_active_schema
from app.services.ui_schema_service import build_ui_jsonschema_for_active_section

router = APIRouter()


@router.get(
    "/schemas/{section_id}/active/raw",
    dependencies=[Depends(require_permission("content:read"))],  # tenant_id via query
)
def get_active_schema_raw(
    section_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
):
    ss = rs_get_active_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        return {"active": None, "message": "No active schema for this section."}
    return {
        "active": {
            "version": ss.version,
            "title": ss.title,
            "is_active": getattr(ss, "is_active", False),
            "schema": ss.schema,
            "created_at": ss.created_at,
        }
    }


@router.get(
    "/schemas/{section_id}/versions",
    dependencies=[Depends(require_permission("content:read"))],  # tenant_id via query
)
def list_schema_versions(
    section_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(SectionSchema)
            .where(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
            )
            .order_by(SectionSchema.version.asc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "version": r.version,
            "title": r.title,
            "is_active": getattr(r, "is_active", False),
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get(
    "/schemas/{section_id}/active/ui",
    dependencies=[Depends(require_permission("content:read"))],  # tenant_id via query
)
def get_active_schema_ui_contract(
    section_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """
    Minimal UI contract for auto-generated forms (backward compatible).
    Returns the JSON Schema “as-is” + basic metadata.
    """
    ss = rs_get_active_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        raise HTTPException(status_code=404, detail="No active schema for this section.")
    return {
        "section_id": section_id,
        "tenant_id": tenant_id,
        "active_version": ss.version,
        "title": ss.title,
        "schema": ss.schema,   # raw JSON Schema
        "widgets": {},         # reserved for future UI mappings
        "hints": {},           # reserved for future help text
    }


@router.get(
    "/schemas/{section_id}/active/ui-json",
    dependencies=[Depends(require_permission("content:read"))],  # tenant_id via query
)
def get_active_schema_ui_json(
    section_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """
    Editor-ready JSON Schema:
      • local $ref expanded
      • registry UI overlays applied into `x-ui`
      • ensures `$version`
    Ideal for the new editor.
    """
    try:
        schema = build_ui_jsonschema_for_active_section(db, tenant_id=tenant_id, section_id=section_id)
        return {
            "section_id": section_id,
            "tenant_id": tenant_id,
            "schema": schema,
        }
    except LookupError:
        raise HTTPException(status_code=404, detail="No active schema for this section.")


