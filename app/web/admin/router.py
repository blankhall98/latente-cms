# app/web/admin/router.py
from __future__ import annotations

from typing import Any, Optional, Dict, Tuple
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant, User, UserTenant, UserTenantStatus, Role
from app.models.content import Section, Entry, SectionSchema
from app.services.passwords import verify_password

# If you want server-side validation, you can switch this on and add jsonschema validator.
ENABLE_SERVER_VALIDATION = False

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(include_in_schema=False)

SESSION_USER_KEY = "user"
SESSION_ACTIVE_TENANT_KEY = "active_tenant"


# ---------------------------
# Helpers (enums & session)
# ---------------------------
def _status_value(enum_cls: Any, *candidates: str) -> Any:
    for name in candidates:
        if hasattr(enum_cls, name):
            val = getattr(enum_cls, name)
            return getattr(val, "value", val)
    return candidates[-1]


def _active_status_value() -> Any:
    return _status_value(UserTenantStatus, "ACTIVE", "Active", "active")


def _require_web_user(request: Request) -> dict:
    user = (request.session or {}).get(SESSION_USER_KEY)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _get_active_tenant(request: Request) -> dict | None:
    return (request.session or {}).get(SESSION_ACTIVE_TENANT_KEY)


def _set_active_tenant(request: Request, tenant_id: int, tenant_slug: str, tenant_name: str) -> None:
    request.session[SESSION_ACTIVE_TENANT_KEY] = {
        "id": int(tenant_id),
        "slug": tenant_slug,
        "name": tenant_name,
    }


def _parse_int(v: Optional[str], default: int) -> int:
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _load_entry_or_404(db: Session, entry_id: int, tenant_id: int) -> tuple[Entry, Section]:
    row = db.execute(
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(and_(Entry.id == entry_id, Entry.tenant_id == tenant_id))
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Page not found in this project")
    return row  # (Entry, Section)


# ---------------------------
# JSON Schema helpers
# ---------------------------
def _get_active_schema(db: Session, section_id: int) -> Optional[SectionSchema]:
    return db.execute(
        select(SectionSchema)
        .where(and_(SectionSchema.section_id == section_id, SectionSchema.is_active == True))  # noqa: E712
        .order_by(SectionSchema.version.desc())
    ).scalars().first()


def _extract_schema_dict(ss: SectionSchema | None) -> dict:
    """Get JSON Schema dict from flexible column naming."""
    if not ss:
        return {}
    for attr in ("json_schema", "schema", "schema_json", "data"):
        if hasattr(ss, attr):
            val = getattr(ss, attr)
            if val is None:
                continue
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    pass
    return {}


def _deep_merge(base: Any, override: Any) -> Any:
    """Recursive deep merge: dict + dict, list replacement, value override."""
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = _deep_merge(base.get(k), v)
        return out
    # For arrays, we don't try to merge per item (schemas usually define items[] shape)
    return override if override is not None else base


def _defaults_from_schema(schema: dict) -> Any:
    """
    Recursively produce a default object for the JSON Schema.
    - Use 'default' when present.
    - For object: build dict from properties (using defaults).
    - For array: default to [] or schema['default'] if present.
    - For primitives: default if present, else sensible empty value.
    """
    if not isinstance(schema, dict):
        return None

    if "default" in schema:
        return schema["default"]

    t = schema.get("type")

    # Handle 'oneOf'/'anyOf' quickly: pick first schema to extract defaults
    for union_key in ("oneOf", "anyOf", "allOf"):
        if union_key in schema and isinstance(schema[union_key], list) and schema[union_key]:
            # naive pick the first as default model
            return _defaults_from_schema(schema[union_key][0])

    if t == "object":
        props = schema.get("properties", {}) or {}
        out: Dict[str, Any] = {}
        for k, sub in props.items():
            out[k] = _defaults_from_schema(sub)
        return out

    if t == "array":
        # Prefer explicit default, otherwise empty array
        if "default" in schema:
            return schema["default"]
        return []

    # primitive fallback
    if t == "string":
        return ""
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False

    # if type is missing, try to infer by presence of 'properties' or 'items'
    if "properties" in schema:
        return _defaults_from_schema({"type": "object", "properties": schema["properties"]})
    if "items" in schema:
        return _defaults_from_schema({"type": "array", **({"default": []} if "default" not in schema else {})})

    return None


def _build_form_model_from_active_schema(json_schema: dict, entry_data: dict) -> Tuple[dict, int]:
    """
    Build the form model we will edit:
      1) compute default model from active schema
      2) deep-merge current entry data on top
    Returns (model, inferred_version) where version is best-effort from schema metadata.
    """
    defaults = _defaults_from_schema(json_schema) or {}
    merged = _deep_merge(defaults, entry_data or {})
    # try to detect version (not strictly required; we will get it from SectionSchema)
    schema_version = json_schema.get("$version") or json_schema.get("version") or 1
    return merged, int(schema_version)


# Minimal validation stub (set ENABLE_SERVER_VALIDATION=True and add jsonschema if desired)
def _validate_against_schema(json_schema: dict, data_obj: dict) -> list[str]:
    if not ENABLE_SERVER_VALIDATION:
        return []
    # Hook up Draft202012Validator here if you want strict validation.
    return []


# ---------------------------
# Web Login
# ---------------------------
@router.get("/login")
def login_get(request: Request):
    if (request.session or {}).get(SESSION_USER_KEY):
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.hashed_password or "") or not user.is_active:
        ctx = {"request": request, "error": "Invalid credentials or inactive user."}
        return templates.TemplateResponse("auth/login.html", ctx, status_code=401)

    request.session[SESSION_USER_KEY] = {
        "id": int(user.id),
        "email": user.email,
        "is_superadmin": bool(user.is_superadmin),
        "full_name": user.full_name or "",
    }

    rows = db.execute(
        select(Tenant, UserTenant, Role)
        .join(UserTenant, UserTenant.tenant_id == Tenant.id)
        .join(Role, Role.id == UserTenant.role_id)
        .where(
            and_(
                UserTenant.user_id == int(user.id),
                UserTenant.status == _active_status_value(),
            )
        )
        .order_by(Tenant.name.asc())
    ).all()

    if len(rows) == 1:
        t, _, _ = rows[0]
        _set_active_tenant(request, t.id, t.slug, t.name)

    return RedirectResponse(url="/admin", status_code=302)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ---------------------------
# Dashboard
# ---------------------------
@router.get("/admin")
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        auth = _require_web_user(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    user_id = int(auth["id"])
    is_superadmin = bool(auth.get("is_superadmin"))
    active = _get_active_tenant(request)

    if not active:
        kpis = [
            {"label": "Pages", "value": "0", "suffix": "published"},
            {"label": "Sections", "value": "0", "suffix": "in project"},
            {"label": "Projects", "value": "0", "suffix": "available"},
        ]
        return templates.TemplateResponse(
            "admin/dashboard.html",
            {
                "request": request,
                "user": {"email": auth.get("email")},
                "kpis": kpis,
                "recent_entries": [],
                "quick_links": [
                    {"href": "/admin/projects", "title": "Browse Projects", "sub": "Switch or set your active project"},
                ],
                "current_tenant": {"name": "—", "slug": None, "id": None},
            },
        )

    tenant_id = int(active["id"])

    # Projects KPI
    if is_superadmin:
        projects_count = db.scalar(select(func.count(Tenant.id)))
    else:
        projects_count = db.scalar(
            select(func.count(UserTenant.tenant_id))
            .where(and_(UserTenant.user_id == user_id, UserTenant.status == _active_status_value()))
        ) or 0

    # Sections KPI
    sections_count = db.scalar(
        select(func.count(Section.id)).where(Section.tenant_id == tenant_id)
    ) or 0

    # Pages (published) KPI
    PUBLISHED = "published"
    pages_published = db.scalar(
        select(func.count(Entry.id)).where(
            and_(Entry.tenant_id == tenant_id, Entry.status == PUBLISHED)
        )
    ) or 0

    kpis = [
        {"label": "Pages", "value": str(pages_published), "suffix": "published"},
        {"label": "Sections", "value": str(sections_count), "suffix": "in project"},
        {"label": "Projects", "value": str(projects_count), "suffix": "available"},
    ]

    # Recent pages
    try:
        order_cols = [Entry.updated_at.desc().nullslast(), Entry.id.desc()]
    except Exception:
        order_cols = [Entry.id.desc()]

    rows = db.execute(
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(Entry.tenant_id == tenant_id)
        .order_by(*order_cols)
        .limit(5)
    ).all()

    recent_entries = []
    for e, s in rows:
        status_text = getattr(e.status, "value", e.status)
        section_key = getattr(s, "key", getattr(s, "name", "Section"))
        tenant_slug = active.get("slug", "")
        title = (e.data or {}).get("title") or e.slug or f"Page {getattr(e, "id", "")}"
        recent_entries.append({
            "title": title,
            "sub": f"{section_key} / {tenant_slug} · {status_text}",
            "id": int(getattr(e, "id", 0)) if getattr(e, "id", None) else None,
        })

    quick_links = [
        {"href": "/admin/projects", "title": "Browse Projects", "sub": "Switch between your projects"},
        {"href": "/admin/pages", "title": "All Pages", "sub": "View and edit pages"},
    ]

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": {"email": auth.get("email")},
            "kpis": kpis,
            "recent_entries": recent_entries,
            "quick_links": quick_links,
            "current_tenant": active,
        },
    )


# ---------------------------
# Projects list & set-active
# ---------------------------
@router.get("/admin/projects")
def projects_list(request: Request, db: Session = Depends(get_db)):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active_val = _active_status_value()

    if is_superadmin:
        rows = db.execute(select(Tenant).order_by(Tenant.name.asc())).all()
        items = [{
            "id": t.id,
            "name": t.name,
            "slug": t.slug,
            "role": "superadmin",
            "role_label": "Superadmin",
            "status": "active",
        } for (t,) in rows]
    else:
        q = (
            select(Tenant, UserTenant, Role)
            .join(UserTenant, UserTenant.tenant_id == Tenant.id)
            .join(Role, Role.id == UserTenant.role_id)
            .where(
                and_(
                    UserTenant.user_id == int(user["id"]),
                    UserTenant.status == active_val,
                )
            )
            .order_by(Tenant.name.asc())
        )
        rows = db.execute(q).all()
        items = []
        for t, ut, r in rows:
            items.append({
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "role": r.key,
                "role_label": getattr(r, "label", r.key).title() if getattr(r, "label", None) else r.key,
                "status": getattr(ut.status, "value", ut.status),
            })

    current = _get_active_tenant(request)
    return templates.TemplateResponse(
        "admin/projects.html",
        {
            "request": request,
            "user": user,
            "projects": items,
            "active_tenant": current,
        },
    )


@router.post("/admin/projects/{tenant_id}/set-active")
def set_active_project(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active_val = _active_status_value()

    if is_superadmin:
        t = db.get(Tenant, tenant_id)
        if not t:
            raise HTTPException(status_code=404, detail="Project not found.")
        _set_active_tenant(request, t.id, t.slug, t.name)
        return RedirectResponse(url="/admin", status_code=303)

    tu = db.execute(
        select(Tenant, UserTenant)
        .where(
            and_(
                Tenant.id == tenant_id,
                UserTenant.tenant_id == Tenant.id,
                UserTenant.user_id == int(user["id"]),
                UserTenant.status == active_val,
            )
        )
    ).first()

    if not tu:
        raise HTTPException(status_code=403, detail="You don’t have access to this project.")

    tenant, _ = tu
    _set_active_tenant(request, tenant.id, tenant.slug, tenant.name)
    return RedirectResponse(url="/admin", status_code=303)


# ---------------------------
# Pages list
# ---------------------------
@router.get("/admin/pages")
def pages_list(
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: Optional[int] = Query(default=None),
):
    user = (request.session or {}).get(SESSION_USER_KEY)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    active = _get_active_tenant(request)
    tid = int(tenant_id or (active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    q: Optional[str] = request.query_params.get("q")
    status_param: Optional[str] = request.query_params.get("status")
    section_id_param: Optional[str] = request.query_params.get("section")
    page = _parse_int(request.query_params.get("page"), 1)
    per_page = _parse_int(request.query_params.get("per_page"), 10)
    page = max(page, 1)
    per_page = max(min(per_page, 50), 5)
    offset = (page - 1) * per_page

    base = (
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(Entry.tenant_id == tid)
    )

    VALID_STATUS = {"published", "draft", "archived"}
    if status_param:
        s = status_param.strip().lower()
        if s in VALID_STATUS:
            base = base.where(Entry.status == s)

    if section_id_param:
        try:
            sid = int(section_id_param)
            base = base.where(Entry.section_id == sid)
        except Exception:
            pass

    if q:
        ilike_term = f"%{q.strip()}%"
        try:
            base = base.where(
                or_(
                    Entry.slug.ilike(ilike_term),
                    Entry.data["title"].astext.ilike(ilike_term),
                )
            )
        except Exception:
            base = base.where(Entry.slug.ilike(ilike_term))

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    try:
        order_cols = [Entry.updated_at.desc().nullslast(), Entry.id.desc()]
    except Exception:
        order_cols = [Entry.id.desc()]
    rows = db.execute(base.order_by(*order_cols).limit(per_page).offset(offset)).all()

    sects = db.execute(
        select(Section.id, Section.name).where(Section.tenant_id == tid).order_by(Section.name.asc())
    ).all()

    items = []
    for e, s in rows:
        title = (e.data or {}).get("title") or e.slug
        items.append({
            "id": e.id,
            "title": title,
            "slug": e.slug,
            "section_name": s.name,
            "status": e.status,
            "updated_at": e.updated_at,
        })

    next_page = page + 1 if (offset + len(items)) < total else None
    prev_page = page - 1 if page > 1 else None

    return templates.TemplateResponse(
        "admin/pages.html",
        {
            "request": request,
            "user": user,
            "active_tenant": active,
            "items": items,
            "sections": [{"id": sid, "name": sname} for sid, sname in sects],
            "filters": {
                "q": q or "",
                "status": (status_param or "").lower(),
                "section": section_id_param or "",
            },
            "page": page,
            "per_page": per_page,
            "next_page": next_page,
            "prev_page": prev_page,
        },
    )


# --------------------------- Page detail (shell) ---------------------------
@router.get("/admin/pages/{entry_id}")
def page_detail(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: Optional[int] = Query(default=None),
    section_tab: Optional[str] = Query(default=None),
):
    user = _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int(tenant_id or (active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    data = entry.data or {}
    keys = list(data.keys())

    preferred_first = ["hero", "header", "intro", "title", "content", "body"]

    def _priority(k: str) -> tuple[int, str]:
        return (preferred_first.index(k) if k in preferred_first else 999, k)

    keys_sorted = sorted(keys, key=_priority)
    current_tab = section_tab or (keys_sorted[0] if keys_sorted else "content")

    sections_nav = [{"key": k, "label": k.replace("_", " ").title()} for k in keys_sorted]
    current_payload = data.get(current_tab, data if current_tab == "content" else "")

    # IMPORTANT: display the active schema version for clarity (no upgrade UI)
    ss_active = _get_active_schema(db, section.id)

    return templates.TemplateResponse(
        "admin/page_detail.html",
        {
            "request": request,
            "user": user,
            "active_tenant": active,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (data.get("title") or data.get("name") or entry.slug),
                "status": entry.status,
                "section_name": section.name,
                "updated_at": entry.updated_at,
                "schema_version": (ss_active.version if ss_active else entry.schema_version),
            },
            "sections_nav": sections_nav,
            "current_tab": current_tab,
            "current_payload": current_payload,
        },
    )


# --------------------------- Page Editor (Active Schema–driven) ---------------------------
@router.get("/admin/pages/{entry_id}/edit")
def page_edit_get(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    # 1) Load ACTIVE schema for this section
    ss = _get_active_schema(db, section.id)
    json_schema = _extract_schema_dict(ss)

    # 2) Build form model from active schema + deep-merge current entry data
    form_model, _ = _build_form_model_from_active_schema(json_schema, entry.data or {})

    # 3) Extract General + Sections for the template
    replace_val = bool((form_model.get("replace") or False))
    seo = form_model.get("seo") or {}
    seo_title = seo.get("title") or ""
    seo_desc = seo.get("description") or ""

    raw_sections = form_model.get("sections") or []
    sections_ui = []
    for i, sec in enumerate(raw_sections):
        t = (sec or {}).get("type") or "Block"
        heading = (sec or {}).get("heading") or ""
        label = f"{i+1:02d} · {t}" + (f" — {heading}" if heading else "")
        sections_ui.append({
            "index": i,
            "label": label,
            "sec": sec,
        })

    return templates.TemplateResponse(
        "admin/page_edit.html",
        {
            "request": request,
            "user": user,
            "active_tenant": active,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (form_model.get("title") or form_model.get("name") or entry.slug),
                "status": entry.status,
                "section_name": section.name,
                # show ACTIVE schema version here (what the form reflects)
                "schema_version": (ss.version if ss else entry.schema_version),
            },
            "replace_val": replace_val,
            "seo_title": seo_title,
            "seo_desc": seo_desc,
            "sections_ui": sections_ui,
            "error": None,
            "ok_message": None,
        },
    )


@router.post("/admin/pages/{entry_id}/edit")
def page_edit_post(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    content_json: str = Form(...),  # built by the serializer (full object)
):
    user = _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    # Load ACTIVE schema for validation + version pin
    ss = _get_active_schema(db, section.id)
    json_schema = _extract_schema_dict(ss)
    active_version = ss.version if ss else entry.schema_version

    # Parse payload
    try:
        parsed = json.loads(content_json)
        if not isinstance(parsed, dict):
            raise ValueError("Submitted payload must be a JSON object.")
    except Exception as e:
        # Rebuild minimal sections UI from existing entry (to show the error gracefully)
        data = entry.data or {}
        sections = data.get("sections") or []
        sections_ui = [{
            "index": i,
            "label": f"{i+1:02d} · {(blk or {}).get('type','Section')}" + (f" — { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
            "json_str": json.dumps(blk or {}, ensure_ascii=False, indent=2),
        } for i, blk in enumerate(sections)]

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
                "request": request,
                "user": user,
                "active_tenant": active,
                "page": {
                    "id": entry.id,
                    "slug": entry.slug,
                    "title": (data.get("title") or data.get("name") or entry.slug),
                    "status": entry.status,
                    "section_name": section.name,
                    "schema_version": active_version,
                },
                "initial_json": content_json,
                "replace_val": bool(data.get("replace", False)),
                "seo_title": (data.get("seo") or {}).get("title", ""),
                "seo_desc": (data.get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
                "error": f"Invalid JSON: {e}",
                "ok_message": None,
            },
            status_code=400,
        )

    # Optional: validate against ACTIVE schema
    errors = _validate_against_schema(json_schema, parsed)
    if errors:
        sections = parsed.get("sections") or []
        sections_ui = [{
            "index": i,
            "label": f"{i+1:02d} · {(blk or {}).get('type','Section')}" + (f" — { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
            "json_str": json.dumps(blk or {}, ensure_ascii=False, indent=2),
        } for i, blk in enumerate(sections)]

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
                "request": request,
                "user": user,
                "active_tenant": active,
                "page": {
                    "id": entry.id,
                    "slug": entry.slug,
                    "title": (parsed.get("title") or (entry.data or {}).get("title") or entry.slug),
                    "status": entry.status,
                    "section_name": section.name,
                    "schema_version": active_version,
                },
                "initial_json": json.dumps(parsed, ensure_ascii=False, indent=2),
                "replace_val": bool(parsed.get("replace", False)),
                "seo_title": (parsed.get("seo") or {}).get("title", ""),
                "seo_desc": (parsed.get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
                "error": "Schema validation failed: " + "; ".join(errors[:5]),
                "ok_message": None,
            },
            status_code=422,
        )

    # Persist: save data and pin entry to ACTIVE schema version (no visible "upgrade")
    entry.data = parsed
    entry.schema_version = active_version
    entry.updated_at = datetime.now(timezone.utc)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    sections = entry.data.get("sections") or []
    sections_ui = [{
        "index": i,
        "label": f"{i+1:02d} · {(blk or {}).get('type','Section')}" + (f" — { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
        "json_str": json.dumps(blk or {}, ensure_ascii=False, indent=2),
    } for i, blk in enumerate(sections)]

    return templates.TemplateResponse(
        "admin/page_edit.html",
        {
            "request": request,
            "user": user,
            "active_tenant": active,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (entry.data or {}).get("title") or entry.slug,
                "status": entry.status,
                "section_name": section.name,
                "schema_version": active_version,
            },
            "initial_json": json.dumps(entry.data or {}, ensure_ascii=False, indent=2),
            "replace_val": bool(entry.data.get("replace", False)),
            "seo_title": (entry.data.get("seo") or {}).get("title", ""),
            "seo_desc": (entry.data.get("seo") or {}).get("description", ""),
            "sections_ui": sections_ui,
            "error": None,
            "ok_message": "Changes saved.",
        },
    )

