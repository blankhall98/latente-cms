# =============================================================================
# Content Endpoints (Sections, Section Schemas, Entries, Publish/Preview)
# =============================================================================
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Header
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.content import (
    SectionCreate, SectionUpdate, SectionOut,
    SectionSchemaCreate, SectionSchemaUpdate, SectionSchemaOut,
    EntryCreate, EntryUpdate, EntryOut,
)
from app.models.content import SectionSchema, Entry
from app.services.content_service import (
    create_section, add_schema_version, set_active_schema,
    create_entry, update_entry, list_entries,
)
from app.services.registry_service import (
    get_registry_for_section,
    get_active_schema as rs_get_active_schema,
    can_activate_version,
)
from app.services.publish_service import (
    transition_entry_status, apply_cache_headers,
)
from app.api.deps.auth import (
    require_permission,              # lo usaremos en endpoints de lectura/edición no problemáticos
    get_current_user_id,
    get_current_user_id_optional,
    # no importar user_has_permission aquí: se usará import tardío
)
from app.security.preview_tokens import (
    create_preview_token, verify_preview_token, PreviewTokenError,
)
from app.core.config import settings

router = APIRouter()


def _get_entry_or_404(db: Session, entry_id: int, tenant_id: int | None) -> Entry:
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if tenant_id is not None and entry.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


# ============================================================================ #
# Sections
# ============================================================================ #
@router.post("/sections", response_model=SectionOut)
def create_section_endpoint(
    payload: SectionCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=payload.tenant_id, perm_key="content:write"):
        raise HTTPException(status_code=403, detail="Missing permission: content:write")

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


# ============================================================================ #
# Section Schemas
# ============================================================================ #
@router.post("/section-schemas", response_model=SectionSchemaOut)
def add_schema_version_endpoint(
    payload: SectionSchemaCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=payload.tenant_id, perm_key="content:write"):
        raise HTTPException(status_code=403, detail="Missing permission: content:write")

    ss = add_schema_version(
        db,
        tenant_id=payload.tenant_id,
        section_id=payload.section_id,
        version=payload.version,
        schema=payload.schema,
        title=payload.title,
        is_active=payload.is_active or False,
    )
    db.commit()
    db.refresh(ss)
    return ss


@router.patch(
    "/section-schemas/{tenant_id}/{section_id}/{version}",
    response_model=SectionSchemaOut,
    dependencies=[Depends(require_permission("content:write"))],
)
def update_schema_endpoint(
    tenant_id: int,
    section_id: int,
    version: int,
    patch: SectionSchemaUpdate,
    db: Session = Depends(get_db),
):
    # Activación de versión (respetando reglas del registry)
    if patch.is_active is True:
        ok, errs = can_activate_version(db, tenant_id=tenant_id, section_id=section_id, target_version=version)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail={"message": "Activation blocked by registry policy", "errors": errs},
            )
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

    # Actualización simple (p. ej., title)
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


# ============================================================================ #
# Lectura auxiliar (registry y schema activo)
# ============================================================================ #
@router.get("/sections/{section_id}/schema-active", dependencies=[Depends(require_permission("content:read"))])
def get_active_schema_endpoint(
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
            "created_at": ss.created_at,
        }
    }


@router.get("/sections/{section_id}/registry", dependencies=[Depends(require_permission("content:read"))])
def get_registry_endpoint(
    section_id: int,
    tenant_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    reg = get_registry_for_section(db, section_id=section_id, tenant_id=tenant_id)
    if not reg:
        return {"registry": None, "message": "No registry declared for this section key."}
    return {"registry": reg}


# ============================================================================ #
# Entries CRUD
# ============================================================================ #
@router.post("/entries", response_model=EntryOut)
def create_entry_endpoint(
    payload: EntryCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=payload.tenant_id, perm_key="content:write"):
        raise HTTPException(status_code=403, detail="Missing permission: content:write")
    try:
        entry = create_entry(db, payload)
        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/entries/{entry_id}",
    response_model=EntryOut,
    dependencies=[Depends(require_permission("content:write"))],
)
def update_entry_endpoint(
    entry_id: int,
    tenant_id: int,
    patch: EntryUpdate,
    db: Session = Depends(get_db),
):
    try:
        entry = update_entry(db, entry_id, tenant_id, patch)
        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/entries",
    response_model=list[EntryOut],
    dependencies=[Depends(require_permission("content:read"))],
)
def list_entries_endpoint(
    tenant_id: int = Query(...),
    section_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return list_entries(
        db,
        tenant_id=tenant_id,
        section_id=section_id,
        status=status,
        limit=limit,
        offset=offset,
    )


# ============================================================================ #
# Publish / Unpublish / Archive  (permiso content:publish, chequeo inline con import tardío)
# ============================================================================ #
@router.post("/entries/{entry_id}/publish", response_model=EntryOut)
def publish_entry(
    entry_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key="content:publish"):
        raise HTTPException(status_code=403, detail="Missing permission: content:publish")
    try:
        entry = _get_entry_or_404(db, entry_id, tenant_id)
        transition_entry_status(db, entry, "published")
        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/entries/{entry_id}/unpublish", response_model=EntryOut)
def unpublish_entry(
    entry_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key="content:publish"):
        raise HTTPException(status_code=403, detail="Missing permission: content:publish")
    try:
        entry = _get_entry_or_404(db, entry_id, tenant_id)
        transition_entry_status(db, entry, "draft")
        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/entries/{entry_id}/archive", response_model=EntryOut)
def archive_entry(
    entry_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key="content:publish"):
        raise HTTPException(status_code=403, detail="Missing permission: content:publish")
    try:
        entry = _get_entry_or_404(db, entry_id, tenant_id)
        transition_entry_status(db, entry, "archived")
        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================ #
# Preview Tokens
# ============================================================================ #
@router.post("/entries/{entry_id}/preview-token")
def issue_preview_token_endpoint(
    entry_id: int,
    tenant_id: int = Query(...),
    expires_in: int | None = Query(None, ge=60, le=86400),
    schema_version: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    current_user_id: int | None = Depends(get_current_user_id_optional),
):
    from app.api.deps import auth as auth_deps

    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not auth_deps.user_has_permission(db, current_user_id, tenant_id, "content:publish"):
        raise HTTPException(status_code=403, detail="Not allowed to issue preview tokens")

    entry = _get_entry_or_404(db, entry_id, tenant_id)
    tok = create_preview_token(
        tenant_id=tenant_id,
        entry_id=entry.id,
        schema_version=schema_version or entry.schema_version,
        expires_in=expires_in,
    )
    return {"token": tok, "expires_in": expires_in or settings.PREVIEW_TOKEN_EXPIRE_SECONDS}


@router.get("/entries/{entry_id}/preview")
def preview_entry_endpoint(
    entry_id: int,
    tenant_id: int | None = Query(None),
    token: str | None = Query(None, description="Preview token (JWT)"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(get_db),
    current_user_id: int | None = Depends(get_current_user_id_optional),
):
    if token:
        try:
            data = verify_preview_token(token)
        except PreviewTokenError as e:
            raise HTTPException(status_code=401, detail=str(e))
        tenant_id = int(data["tenant_id"])
        entry_id = int(data["entry_id"])
        forced_schema_version = int(data.get("schema_version", 0)) or None
    else:
        if not current_user_id:
            raise HTTPException(status_code=401, detail="Authentication required")
        forced_schema_version = None

    entry = _get_entry_or_404(db, entry_id, tenant_id)

    import hashlib, json
    payload = {
        "id": entry.id,
        "tenant_id": entry.tenant_id,
        "section_id": entry.section_id,
        "slug": entry.slug,
        "status": entry.status,
        "schema_version": forced_schema_version or entry.schema_version,
        "data": entry.data,
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    etag = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if if_none_match and if_none_match == etag:
        resp = Response(status_code=304)
        apply_cache_headers(resp, status=entry.status)
        resp.headers["ETag"] = etag
        return resp

    resp = Response(content=body, media_type="application/json")
    resp.headers["ETag"] = etag
    apply_cache_headers(resp, status=entry.status)
    return resp


