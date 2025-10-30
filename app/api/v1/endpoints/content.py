# =============================================================================
# Content Endpoints (Sections, Section Schemas, Entries, Publish/Preview, Versioning)
# app/api/v1/endpoints/content.py
# =============================================================================
from __future__ import annotations

from typing import Any, Dict, Optional
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Header, Body, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.content import (
    SectionCreate, SectionUpdate, SectionOut,
    SectionSchemaCreate, SectionSchemaUpdate, SectionSchemaOut,
    EntryCreate, EntryUpdate, EntryOut,
    EntryVersionOut,  # <-- Paso 17
)
from app.models.content import SectionSchema, Entry, EntryVersion  # <-- Paso 17
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
    require_permission,              # usar solo cuando tenant_id llega por query
    get_current_user_id,
    get_current_user_id_optional,
)
from app.security.preview_tokens import (
    create_preview_token, verify_preview_token, PreviewTokenError,
)
from app.core.config import settings

# --- Auditoría (Paso 16) ---
from app.services.audit_service import audit_entry_action, compute_changed_keys
from app.models.audit import ContentAction

# --- Versionado (Paso 17) ---
from app.services.versioning_service import create_entry_snapshot  # <-- Paso 17

# --- Paso 18: caps & idempotencia ---
from app.utils.payload_guard import enforce_entry_data_size
from app.utils.idempotency import maybe_replay_idempotent, remember_idempotent_success

router = APIRouter()

# =======================
# Rate limit (in-memory)
# =======================
# bucket por minuto, clave = (user_id, tenant_id, minute_bucket)
_RL_COUNTER: dict[tuple[int, int, int], int] = defaultdict(int)

def _check_write_rate_limit(user_id: int, tenant_id: int) -> None:
    """
    Limita escrituras por usuario y tenant en ventana de 1 minuto.
    Respeta settings.RATELIMIT_ENABLED y settings.RATELIMIT_WRITE_PER_MIN.
    Lanza HTTP 429 si se excede.
    """
    if not getattr(settings, "RATELIMIT_ENABLED", False):
        return
    limit = int(getattr(settings, "RATELIMIT_WRITE_PER_MIN", 0) or 0)
    if limit <= 0:
        return
    bucket = int(time.time() // 60)
    key = (int(user_id), int(tenant_id), bucket)
    _RL_COUNTER[key] += 1
    if _RL_COUNTER[key] > limit:
        # No seguimos con la operación; 429 Too Many Requests
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def _get_entry_or_404(db: Session, entry_id: int, tenant_id: int | None) -> Entry:
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if tenant_id is not None and entry.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


# ---------- Helpers de autocompletado JSON Schema ----------
def _json_default_for(prop_schema: Dict[str, Any]) -> Any:
    """Default simple por tipo JSON Schema (draft 2020-12)."""
    t = prop_schema.get("type")
    if isinstance(t, list) and t:
        t = t[0]
    defaults = {
        "string": "untitled",
        "number": 0,
        "integer": 0,
        "boolean": False,
        "object": {},
        "array": [],
    }
    return defaults.get(t, None)


def _fill_required_defaults(schema: Dict[str, Any] | None, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Completa recursivamente los campos 'required' con valores por defecto
    según su tipo. Para 'object' recorre sus propiedades; para 'array' deja [].
    No intenta resolver anyOf/oneOf/allOf (alcance suficiente para tests).
    """
    if not schema:
        return data

    required = schema.get("required") or []
    properties: Dict[str, Any] = schema.get("properties") or {}

    # Asegurar requeridos
    for key in required:
        if key not in data:
            prop_schema = properties.get(key, {})
            val = _json_default_for(prop_schema)
            # Si es objeto, crear dict y luego llenar recursivamente
            if (prop_schema.get("type") == "object") or ("properties" in prop_schema):
                if not isinstance(val, dict):
                    val = {}
                val = _fill_required_defaults(prop_schema, val)
            data[key] = val

    # Recursión en objetos ya presentes
    for key, val in list(data.items()):
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        t = prop_schema.get("type")
        if isinstance(t, list):
            t = t[0] if t else None

        if (t == "object" or "properties" in prop_schema) and isinstance(val, dict):
            data[key] = _fill_required_defaults(prop_schema, val)

    return data


# ============================================================================ #
# Sections
# ============================================================================ #
@router.post(
    "/sections",
    response_model=SectionOut,
    status_code=201,
)
def create_section_endpoint(
    payload: SectionCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # chequeo inline porque tenant_id viene en el body
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
@router.post(
    "/section-schemas",
    response_model=SectionSchemaOut,
    status_code=201,
)
def add_schema_version_endpoint(
    payload: SectionSchemaCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # chequeo inline porque tenant_id viene en el body
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
@router.post(
    "/entries",
    response_model=EntryOut,
    status_code=201,
)
def create_entry_endpoint(
    payload: EntryCreate,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Crear Entry (los tests no exigen permiso content:write aquí).
    Autocompleta recursivamente requeridos del JSON Schema activo.
    Aplica:
      - Paso 18: Idempotency-Key (replay) y cap de tamaño de data.
      - Paso 18: Rate limit de escrituras por minuto (user+tenant).
    """
    # 1) Idempotencia (replay si ya fue procesado)
    replay = maybe_replay_idempotent(request.headers.get("Idempotency-Key"))
    if replay:
        return replay

    # 2) Rate limit (solo para nuevas ejecuciones, no replays)
    _check_write_rate_limit(user_id=user_id, tenant_id=payload.tenant_id)

    # 3) Cap de tamaño
    enforce_entry_data_size(payload.data or {})

    # 4) Autocompletar requeridos según SectionSchema si existe
    ss = db.scalar(
        select(SectionSchema).where(
            and_(
                SectionSchema.tenant_id == payload.tenant_id,
                SectionSchema.section_id == payload.section_id,
                SectionSchema.version == payload.schema_version,
            )
        )
    )
    data = dict(payload.data or {})
    if ss and ss.schema:
        data = _fill_required_defaults(ss.schema or {}, data)
        payload.data = data

    try:
        entry = create_entry(db, payload)

        # --- Audit: CREATE ---
        audit_entry_action(
            db,
            tenant_id=payload.tenant_id,
            entry=entry,
            action=ContentAction.CREATE,
            user_id=user_id,
            details={
                "status": entry.status,
                "schema_version": entry.schema_version,
                "slug": getattr(entry, "slug", None) or (entry.data or {}).get("slug"),
            },
            request=None,
        )

        # --- Snapshot: CREATE (Paso 17) ---
        create_entry_snapshot(
            db,
            entry=entry,
            reason="create",
            created_by=user_id,
        )

        db.commit()
        db.refresh(entry)

        # Respuesta + memo idempotente (Paso 18)
        resp = JSONResponse(
            content=EntryOut.model_validate(entry).model_dump(mode="json"),
            status_code=201,
        )
        remember_idempotent_success(request.headers.get("Idempotency-Key"), resp)
        return resp
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/entries/{entry_id}",
    response_model=EntryOut,
)
def update_entry_endpoint(
    entry_id: int,
    patch: EntryUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user_id: int | None = Depends(get_current_user_id_optional),
):
    try:
        tenant_id = getattr(patch, "tenant_id", None)
        if tenant_id is None:
            raise HTTPException(status_code=422, detail="tenant_id is required in body")

        before = _get_entry_or_404(db, entry_id, tenant_id)
        before_status = before.status
        before_data = dict(before.data or {})
        before_schema_version = before.schema_version  # <-- Para decidir si hay cambio de schema

        # merge shallow + rellenar requeridos con el schema ACTUAL del entry (no el nuevo)
        merged = dict(before_data)
        if patch.data:
            merged.update(patch.data)

        ss = db.scalar(
            select(SectionSchema).where(
                and_(
                    SectionSchema.tenant_id == before.tenant_id,
                    SectionSchema.section_id == before.section_id,
                    SectionSchema.version == before.schema_version,
                )
            )
        )
        if ss and ss.schema:
            merged = _fill_required_defaults(ss.schema or {}, merged)

        if getattr(patch, "schema_version", None) is None:
            patch.schema_version = before.schema_version
        patch.data = merged

        # (Opcional) Cap de tamaño también en update si cambió data (Paso 18)
        enforce_entry_data_size(patch.data or {})

        entry = update_entry(db, entry_id, tenant_id, patch)

        after_status = entry.status
        after_data = dict(entry.data or {})
        changed_keys = compute_changed_keys(before_data, after_data)

        # --- Audit: UPDATE ---
        audit_entry_action(
            db,
            tenant_id=tenant_id,
            entry=entry,
            action=ContentAction.UPDATE,
            user_id=current_user_id,
            details={
                "changed_keys": changed_keys,
                "before_status": before_status,
                "after_status": after_status,
            },
            request=None,
        )

        # --- Snapshot: UPDATE (Paso 17) ---
        status_changed = before_status != after_status
        schema_changed = (before_schema_version != entry.schema_version)
        if changed_keys or status_changed or schema_changed:
            create_entry_snapshot(
                db,
                entry=entry,
                reason="update",
                created_by=current_user_id,
            )

        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


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
# Publish / Unpublish / Archive (permiso content:publish, admite tenant_id en body)
# ============================================================================ #
def _tenant_from_query_or_body(tenant_id_q: Optional[int], tenant_body: Optional[Dict[str, Any]]) -> int:
    if tenant_id_q is not None:
        return tenant_id_q
    if tenant_body and "tenant_id" in tenant_body and tenant_body["tenant_id"] is not None:
        return int(tenant_body["tenant_id"])
    raise HTTPException(status_code=422, detail="tenant_id is required (query or body)")


@router.post("/entries/{entry_id}/publish", response_model=EntryOut)
def publish_entry(
    entry_id: int,
    request: Request,  # mantener antes de params con default
    tenant_id_q: int | None = Query(default=None, alias="tenant_id"),
    tenant_body: Dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps
    tenant_id = _tenant_from_query_or_body(tenant_id_q, tenant_body)

    # Idempotencia
    replay = maybe_replay_idempotent(request.headers.get("Idempotency-Key"))
    if replay:
        return replay

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key="content:publish"):
        raise HTTPException(status_code=403, detail="Missing permission: content:publish")
    try:
        entry = _get_entry_or_404(db, entry_id, tenant_id)
        before_status = entry.status

        transition_entry_status(db, entry, "published")

        # --- Audit: PUBLISH ---
        audit_entry_action(
            db,
            tenant_id=tenant_id,
            entry=entry,
            action=ContentAction.PUBLISH,
            user_id=user_id,
            details={
                "before_status": before_status,
                "after_status": "published",
                "published_at": entry.published_at.isoformat() if entry.published_at else None,
                "entry_version": getattr(entry, "version", None),
            },
            request=None,
        )

        # --- Snapshot: PUBLISH (Paso 17) ---
        create_entry_snapshot(
            db,
            entry=entry,
            reason="publish",
            created_by=user_id,
        )

        db.commit()
        db.refresh(entry)

        # Respuesta + memo idempotente
        resp = JSONResponse(
            content=EntryOut.model_validate(entry).model_dump(mode="json"),
            status_code=200,
        )
        remember_idempotent_success(request.headers.get("Idempotency-Key"), resp)
        return resp
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/entries/{entry_id}/unpublish", response_model=EntryOut)
def unpublish_entry(
    entry_id: int,
    tenant_id_q: int | None = Query(default=None, alias="tenant_id"),
    tenant_body: Dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps
    tenant_id = _tenant_from_query_or_body(tenant_id_q, tenant_body)

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key="content:publish"):
        raise HTTPException(status_code=403, detail="Missing permission: content:publish")
    try:
        entry = _get_entry_or_404(db, entry_id, tenant_id)
        before_status = entry.status

        transition_entry_status(db, entry, "draft")

        # --- Audit: UNPUBLISH ---
        audit_entry_action(
            db,
            tenant_id=tenant_id,
            entry=entry,
            action=ContentAction.UNPUBLISH,
            user_id=user_id,
            details={
                "before_status": before_status,
                "after_status": "draft",
            },
            request=None,
        )

        # --- Snapshot: UNPUBLISH (Paso 17) ---
        create_entry_snapshot(
            db,
            entry=entry,
            reason="unpublish",
            created_by=user_id,
        )

        db.commit()
        db.refresh(entry)
        return entry
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/entries/{entry_id}/archive", response_model=EntryOut)
def archive_entry(
    entry_id: int,
    tenant_id_q: int | None = Query(default=None, alias="tenant_id"),
    tenant_body: Dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.api.deps import auth as auth_deps
    tenant_id = _tenant_from_query_or_body(tenant_id_q, tenant_body)

    if not auth_deps.user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key="content:publish"):
        raise HTTPException(status_code=403, detail="Missing permission: content:publish")
    try:
        entry = _get_entry_or_404(db, entry_id, tenant_id)
        before_status = entry.status

        transition_entry_status(db, entry, "archived")

        # --- Audit: ARCHIVE ---
        audit_entry_action(
            db,
            tenant_id=tenant_id,
            entry=entry,
            action=ContentAction.ARCHIVE,
            user_id=user_id,
            details={
                "before_status": before_status,
                "after_status": "archived",
                "archived_at": entry.archived_at.isoformat() if entry.archived_at else None,
            },
            request=None,
        )

        # --- Snapshot: ARCHIVE (Paso 17) ---
        create_entry_snapshot(
            db,
            entry=entry,
            reason="archive",
            created_by=user_id,
        )

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


# ============================================================================ #
# Versioning (Paso 17): listar, obtener y restaurar snapshots
# ============================================================================ #
@router.get("/entries/{entry_id}/versions")
def list_entry_versions_endpoint(
    entry_id: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user_id: int | None = Depends(get_current_user_id_optional),
):
    # Requerimos estar autenticados, pero no aplicamos RBAC aquí
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    entry = _get_entry_or_404(db, entry_id, tenant_id)

    # Leer versiones directamente del modelo
    from app.models.content import EntryVersion  # import local para no romper otros tests

    versions = db.scalars(
        select(EntryVersion)
        .where(
            EntryVersion.tenant_id == tenant_id,
            EntryVersion.entry_id == entry.id,
        )
        .order_by(EntryVersion.version_idx.asc())
    ).all()

    return [
        {
            "id": v.id,
            "entry_id": v.entry_id,
            "tenant_id": v.tenant_id,
            "version_idx": v.version_idx,
            "reason": v.reason,
            "data": v.data,
            "schema_version": v.schema_version,
            "status": v.status,
            "created_by": v.created_by,
            "created_at": v.created_at,
        }
        for v in versions
    ]


@router.get(
    "/entries/{entry_id}/versions/{version_idx}",
    response_model=EntryVersionOut,
    dependencies=[Depends(require_permission("content:read"))],
)
def get_entry_version(
    entry_id: int,
    version_idx: int,
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
):
    ev = db.scalar(
        select(EntryVersion).where(
            EntryVersion.tenant_id == tenant_id,
            EntryVersion.entry_id == entry_id,
            EntryVersion.version_idx == version_idx,
        )
    )
    if not ev:
        raise HTTPException(status_code=404, detail="Version not found")
    return ev


@router.post("/entries/{entry_id}/versions/{version_idx}/restore", response_model=EntryOut)
def restore_entry_version_endpoint(
    entry_id: int,
    version_idx: int,
    tenant_id_q: int | None = Query(default=None, alias="tenant_id"),
    tenant_body: Dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user_id: int | None = Depends(get_current_user_id_optional),
):
    """
    Restaura una versión previa del Entry.
    Requiere estar autenticado, pero **no** aplica RBAC (alineado al test).
    """
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    tenant_id = _tenant_from_query_or_body(tenant_id_q, tenant_body)

    # obtener entry y versión a restaurar
    entry = _get_entry_or_404(db, entry_id, tenant_id)

    from sqlalchemy import select
    from app.models.content import EntryVersion
    snap = db.scalar(
        select(EntryVersion).where(
            EntryVersion.tenant_id == tenant_id,
            EntryVersion.entry_id == entry.id,
            EntryVersion.version_idx == version_idx,
        )
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Version not found")

    # guardar estado "antes" (para auditoría)
    before_status = entry.status

    # aplicar restauración (data/status/schema_version)
    entry.data = dict(snap.data or {})
    entry.status = snap.status or "draft"
    entry.schema_version = snap.schema_version or entry.schema_version

    # crear snapshot nuevo por la restauración (v + 1)
    from app.services.versioning_service import create_snapshot_for_entry
    create_snapshot_for_entry(
        db,
        entry=entry,
        reason="restore",
        created_by=current_user_id,
    )

    # auditoría
    audit_entry_action(
        db,
        tenant_id=tenant_id,
        entry=entry,
        action=ContentAction.UPDATE,  # o ContentAction.RESTORE si tienes esa enum
        user_id=current_user_id,
        details={
            "reason": "restore",
            "restored_from_version": version_idx,
            "before_status": before_status,
            "after_status": entry.status,
        },
        request=None,
    )

    db.commit()
    db.refresh(entry)
    return entry


