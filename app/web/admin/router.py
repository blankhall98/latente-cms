# app/web/admin/router.py
from __future__ import annotations

from typing import Any, Optional, Dict, Tuple
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant, UserTenant, UserTenantStatus, Role
from app.models.content import Section, Entry, SectionSchema

# Enriched JSON Schema (with x-ui) for the auto-form
from app.services.ui_schema_service import (
    build_ui_jsonschema_for_active_section,
    build_sections_ui_fallback_for_object_page,  # NEW
)

# Optional server-side schema validation toggle
ENABLE_SERVER_VALIDATION = False

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(include_in_schema=False)

# Must match auth router
SESSION_USER_KEY = "user"
SESSION_ACTIVE_TENANT_KEY = "active_tenant"


# --------------------------- Helpers ---------------------------
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


def _set_single_project_flag(request: Request, db: Session, user: dict, projects_count: int | None = None) -> None:
    """
    Flag used by templates to hide the Projects nav when a non-superadmin has only one project.
    If projects_count is provided, avoids re-querying.
    """
    try:
        is_superadmin = bool(user.get("is_superadmin"))
    except Exception:
        is_superadmin = False
    if is_superadmin:
        request.session.pop("hide_projects_nav", None)
        return
    if projects_count is None:
        user_id = int(user["id"])
        projects_count = db.scalar(
            select(func.count(UserTenant.tenant_id))
            .where(and_(UserTenant.user_id == user_id, UserTenant.status == _active_status_value()))
        ) or 0
    request.session["hide_projects_nav"] = (projects_count == 1)


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


# --------------------------- JSON Schema helpers ---------------------------
def _get_active_schema(db: Session, section_id: int) -> Optional[SectionSchema]:
    return db.execute(
        select(SectionSchema)
        .where(and_(SectionSchema.section_id == section_id, SectionSchema.is_active == True))  # noqa: E712
        .order_by(SectionSchema.version.desc())
    ).scalars().first()


def _extract_schema_dict(ss: SectionSchema | None) -> dict:
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
    # dict <- dict
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = _deep_merge(base.get(k), v)
        return out

    # list <- list (override wins wholesale)
    if isinstance(base, list) and isinstance(override, list):
        return override

    # override wins only if not None
    return override if override is not None else base


def _normalize_projects_payload(payload: Any) -> dict:
    """
    Flatten __draft nesting and ensure projects is a list, not an object with a projects key.
    """
    data = payload or {}
    if not isinstance(data, dict):
        return {}
    cur = data
    # unwrap nested __draft
    while isinstance(cur, dict) and "__draft" in cur and isinstance(cur["__draft"], dict):
        cur = cur["__draft"]
    if not isinstance(cur, dict):
        return {}
    out = dict(cur)
    proj = out.get("projects")
    if isinstance(proj, dict) and "projects" in proj:
        out["projects"] = proj.get("projects") if isinstance(proj.get("projects"), list) else []
    elif proj is None:
        out["projects"] = []
    return out


def _render_projects_data(data: Any) -> dict:
    """
    Merge published root projects with draft projects for display.
    Draft entries with the same title override the published ones; otherwise both are shown.
    """
    if not isinstance(data, dict):
        return {"projects": []}

    # Keep published root separate from draft; do NOT unwrap __draft when building root
    root_payload = dict(data)
    if "__draft" in root_payload:
        root_payload = {k: v for k, v in root_payload.items() if k != "__draft"}
    root = _normalize_projects_payload(root_payload)

    draft_raw = data.get("__draft") if isinstance(data.get("__draft"), dict) else None
    draft = _normalize_projects_payload(draft_raw) if draft_raw else {}

    combined = []
    index_by_title = {}

    def add_list(arr):
        if not isinstance(arr, list):
            return
        for proj in arr:
            if not isinstance(proj, dict):
                continue
            title_key = (proj.get("title") or "").strip().lower()
            if title_key and title_key in index_by_title:
                combined[index_by_title[title_key]] = proj
            else:
                index_by_title[title_key] = len(combined) if title_key else len(combined)
                combined.append(proj)

    add_list(root.get("projects"))
    add_list(draft.get("projects"))

    out = dict(root)
    out["projects"] = combined
    if draft.get("seo"):
        out["seo"] = draft["seo"]
    if "replace" in draft:
        out["replace"] = draft["replace"]
    return out


def _render_home_data(data: Any) -> dict:
    """
    For Home page, merge draft over root so featuredProjects etc. stay visible after save.
    """
    if not isinstance(data, dict):
        return {}
    root = data if isinstance(data, dict) else {}
    draft = root.get("__draft") if isinstance(root.get("__draft"), dict) else None
    if draft:
        merged = _deep_merge(root, draft)
        merged.pop("__draft", None)
        return merged
    return root


def _normalize_privacy_payload(payload: Any, existing: dict | None = None) -> dict:
    """
    Ensure privacy policy payload is a simple object with a string body and optional seo/replace.
    Avoid wiping content when an empty/invalid payload arrives.
    """
    base = existing or {}
    if not isinstance(base, dict):
        base = {}
    out: Dict[str, Any] = {}

    if isinstance(payload, dict):
        body_val = payload.get("body", payload.get("content"))
        out["body"] = "" if body_val is None else str(body_val)
        if "seo" in payload and isinstance(payload.get("seo"), dict):
            out["seo"] = payload["seo"]
        elif isinstance(base.get("seo"), dict):
            out["seo"] = base["seo"]
        out["replace"] = bool(payload.get("replace", base.get("replace", False)))
    else:
        out["body"] = str(payload) if payload is not None else ""
        if isinstance(base.get("seo"), dict):
            out["seo"] = base["seo"]
        out["replace"] = bool(base.get("replace", False))

    # Fallback to existing body if incoming is empty and existing had content
    if (out.get("body", "") == "") and isinstance(base.get("body"), str) and base.get("body"):
        out["body"] = base["body"]

    return out


def _defaults_from_schema(schema: dict) -> Any:
    if not isinstance(schema, dict):
        return None

    if "default" in schema:
        return schema["default"]

    t = schema.get("type")

    for union_key in ("oneOf", "anyOf", "allOf"):
        if union_key in schema and isinstance(schema[union_key], list) and schema[union_key]:
            return _defaults_from_schema(schema[union_key][0])

    if t == "object":
        props = schema.get("properties", {}) or {}
        out: Dict[str, Any] = {}
        for k, sub in props.items():
            out[k] = _defaults_from_schema(sub)
        return out

    if t == "array":
        if "default" in schema:
            return schema["default"]
        return []

    if t == "string":
        return ""
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False

    if "properties" in schema:
        return _defaults_from_schema({"type": "object", "properties": schema["properties"]})
    if "items" in schema:
        return _defaults_from_schema({"type": "array", **({"default": []} if "default" not in schema else {})})

    return None


def _build_form_model_from_active_schema(json_schema: dict, entry_data: dict) -> Tuple[dict, int]:
    defaults = _defaults_from_schema(json_schema) or {}
    merged = _deep_merge(defaults, entry_data or {})
    schema_version = json_schema.get("$version") or json_schema.get("version") or 1
    return merged, int(schema_version)


def _validate_against_schema(json_schema: dict, data_obj: dict) -> list[str]:
    if not ENABLE_SERVER_VALIDATION:
        return []
    # Hook: integrate Draft 2020-12 if you want strict server-side validation
    return []


# --------------------------- Dashboard ---------------------------
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
                "current_tenant": {"name": "-", "slug": None, "id": None},
            },
        )

    tenant_id = int(active["id"])

    if is_superadmin:
        projects_count = db.scalar(select(func.count(Tenant.id)))
    else:
        projects_count = db.scalar(
            select(func.count(UserTenant.tenant_id))
            .where(and_(UserTenant.user_id == user_id, UserTenant.status == _active_status_value()))
        ) or 0

    _set_single_project_flag(request, db, auth, projects_count)

    sections_count = db.scalar(
        select(func.count(Section.id)).where(Section.tenant_id == tenant_id)
    ) or 0

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
        title = (e.data or {}).get("title") or e.slug or f"Page {getattr(e, 'id', '')}"
        recent_entries.append({
            "title": title,
            "sub": f"{section_key} / {tenant_slug} - {status_text}",
            "id": int(getattr(e, "id", 0)) if getattr(e, "id", None) else None,
        })

    quick_links = [
        {"href": "/admin/projects", "title": "Browse Projects", "sub": "Switch between your projects"},
        {"href": "/admin/pages", "title": "All Pages", "sub": "View and edit pages"},
    ]
    if (not is_superadmin) and projects_count == 1:
        quick_links = [
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


# --------------------------- Projects ---------------------------
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

    _set_single_project_flag(request, db, user, len(items))

    if (not is_superadmin) and len(items) == 1:
        only = items[0]
        _set_active_tenant(request, only["id"], only["slug"], only["name"])
        return RedirectResponse(url="/admin/pages", status_code=302)

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
        raise HTTPException(status_code=403, detail="You don't have access to this project.")

    tenant, _ = tu
    _set_active_tenant(request, tenant.id, tenant.slug, tenant.name)
    return RedirectResponse(url="/admin", status_code=303)


# --------------------------- Pages list ---------------------------
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


# --------------------------- Page detail (read-only shell) ---------------------------
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


# --------------------------- Page Editor (Active Schema-driven) ---------------------------
@router.get("/admin/pages/{entry_id}/edit")
def page_edit_get(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    # If page is published and has __draft, edit the draft (except projects)
    base_data = entry.data or {}
    is_published = (getattr(entry, "status", "draft") == "published")
    if section.key == "projects":
        working_data = _render_projects_data(base_data)
    elif section.key == "privacy_policy":
        # Prefer draft if exists, but always normalize to a simple object
        working_candidate = base_data.get("__draft") if (is_published and isinstance(base_data.get("__draft"), dict)) else base_data
        working_data = _normalize_privacy_payload(working_candidate, base_data if isinstance(base_data, dict) else {})
    elif section.key == "home":
        working_data = _render_home_data(base_data)
    else:
        working_data = (base_data.get("__draft") if (is_published and isinstance(base_data.get("__draft"), dict)) else base_data)

    # UI JSON Schema (enriched) for auto-form
    try:
        schema_ui_dict = build_ui_jsonschema_for_active_section(db, tenant_id=tid, section_id=section.id)
        schema_ui_json = json.dumps(schema_ui_dict, ensure_ascii=False)
        ss_version = schema_ui_dict.get("$version") or schema_ui_dict.get("version")
    except Exception:
        schema_ui_json = ""
        ss_version = None

    # Initial model (defaults merged with current data)
    ss = _get_active_schema(db, section.id)
    json_schema = _extract_schema_dict(ss)
    form_model, _ = _build_form_model_from_active_schema(json_schema, working_data or {})

    replace_val = bool((form_model.get("replace") or False))
    seo = form_model.get("seo") or {}
    seo_title = seo.get("title") or ""
    seo_desc = seo.get("description") or ""

    raw_sections = form_model.get("sections") or []
    sections_ui = []
    if section.key == "privacy_policy":
        # Single body field; force a simple panel keyed to privacy_policy with body inside
        sections_ui = [{
            "index": 0,
            "label": "01 - Privacy Policy",
            "sec": {"body": form_model.get("body", "")},
            "key": "privacy_policy",
        }]
    elif section.key == "projects":
        sections_ui = [{
            "index": 0,
            "label": "01 - Projects",
            "sec": {"projects": form_model.get("projects", [])},
            "key": "projects",
        }]
    elif isinstance(raw_sections, list) and raw_sections:
        for i, sec in enumerate(raw_sections):
            t = (sec or {}).get("type") or "Block"
            heading = (sec or {}).get("heading") or ""
            label = f"{i+1:02d} - {t}" + (f" | {heading}" if heading else "")
            sections_ui.append({"index": i, "label": label, "sec": sec, "key": (sec or {}).get("type") })
    else:
        # Fallback for object-style pages (ANRO)
        sections_ui = build_sections_ui_fallback_for_object_page(form_model)

    if getattr(section, "key", "") == "home":
        entry_json_for_client = _render_home_data(entry.data)
    elif getattr(section, "key", "") == "projects":
        entry_json_for_client = _render_projects_data(entry.data)
    else:
        entry_json_for_client = working_data if working_data is not None else (entry.data or {})

    return templates.TemplateResponse(
        "admin/page_edit.html",
        {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (form_model.get("title") or form_model.get("name") or entry.slug),
                "status": entry.status,
                "section_name": section.name,
                "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                "schema_version": (ss.version if ss else entry.schema_version),
                "section_id": int(section.id),
            },
            "replace_val": replace_val,
            "seo_title": seo_title,
            "seo_desc": seo_desc,
            "sections_ui": sections_ui,
            "schema_ui_json": schema_ui_json,  # serialized
            "error": None,
            "ok_message": None,
            "__entry_data_json": json.dumps(entry_json_for_client or {}, ensure_ascii=False),
        },
    )


@router.post("/admin/pages/{entry_id}/edit")
def page_edit_post(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    content_json: str = Form(...),
):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    # UI JSON Schema (also for POST)
    try:
        schema_ui_dict = build_ui_jsonschema_for_active_section(db, tenant_id=tid, section_id=section.id)
        schema_ui_json = json.dumps(schema_ui_dict, ensure_ascii=False)
        ui_version = schema_ui_dict.get("$version") or schema_ui_dict.get("version")
    except Exception:
        schema_ui_json = ""
        ui_version = None

    ss = _get_active_schema(db, section.id)
    json_schema = _extract_schema_dict(ss)
    active_version = (ui_version if ui_version is not None else (ss.version if ss else entry.schema_version))

    # --- Safe parse
    try:
        parsed = json.loads(content_json)
        if not isinstance(parsed, dict):
            raise ValueError("Submitted payload must be a JSON object.")
    except Exception as e:
        data = entry.data or {}
        sections = data.get("sections") or []
        sections_ui = []
        if sections:
            sections_ui = [{
                "index": i,
                "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
                "sec": (blk or {}),
            } for i, blk in enumerate(sections)]
        else:
            sections_ui = build_sections_ui_fallback_for_object_page(data)

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (data.get("title") or data.get("name") or entry.slug),
                "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": content_json,
                "replace_val": bool(data.get("replace", False)),
                "seo_title": (data.get("seo") or {}).get("title", ""),
                "seo_desc": (data.get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
                "schema_ui_json": schema_ui_json,
                "error": f"Invalid JSON: {e}",
                "ok_message": None,
            },
            status_code=400,
        )

    # --- Minimal validation (pluggable)
    errors = _validate_against_schema(json_schema, parsed)
    if errors:
        sections = parsed.get("sections") or []
        sections_ui = []
        if sections:
            sections_ui = [{
                "index": i,
                "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
                "sec": (blk or {}),
            } for i, blk in enumerate(sections)]
        else:
            sections_ui = build_sections_ui_fallback_for_object_page(parsed)

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (parsed.get("title") or (entry.data or {}).get("title") or entry.slug),
                "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": json.dumps(parsed, ensure_ascii=False, indent=2),
                "replace_val": bool(parsed.get("replace", False)),
                "seo_title": (parsed.get("seo") or {}).get("title", ""),
                "seo_desc": (parsed.get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
                "schema_ui_json": schema_ui_json,
                "error": "Schema validation failed: " + "; ".join(errors[:5]),
                "ok_message": None,
            },
            status_code=422,
        )

    # ----------------------------- Anti-wipe rules -----------------------------
    base_data = entry.data or {}
    payload = parsed or {}
    is_published_now = (getattr(entry, "status", "draft") == "published")

    # Special handling: projects section
    if getattr(section, "key", "") == "projects":
        base_projects_data = _normalize_projects_payload(entry.data or {})
        incoming_projects = _normalize_projects_payload(payload)
        if not incoming_projects.get("projects"):
            incoming_projects["projects"] = base_projects_data.get("projects", [])
        if "seo" not in incoming_projects and base_projects_data.get("seo"):
            incoming_projects["seo"] = base_projects_data["seo"]
        if "replace" not in incoming_projects:
            incoming_projects["replace"] = False

        now = datetime.now(timezone.utc)
        if is_published_now:
            base_clean = dict(entry.data) if isinstance(entry.data, dict) else {}
            base_clean.pop("__draft", None)
            base_clean["__draft"] = incoming_projects
            entry.data = base_clean
        else:
            entry.data = incoming_projects
        entry.schema_version = active_version
        entry.updated_at = now
        db.add(entry)
        db.commit()
        db.refresh(entry)

        working_after = _render_projects_data(entry.data)
        sections_ui = [{
            "index": 0,
            "label": "01 - Projects",
            "sec": {"projects": (working_after or {}).get("projects", [])},
            "key": "projects",
        }]
        entry_json_for_client = _render_projects_data(entry.data)
        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
                "request": request,
                "user": {"id": int(user["id"]), "email": user.get("email")},
                "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
                "is_superadmin": is_superadmin,
                "page": {
                    "id": entry.id,
                    "slug": entry.slug,
                    "title": (working_after or {}).get("title") or entry.slug,
                    "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": json.dumps(working_after or {}, ensure_ascii=False, indent=2),
                "replace_val": bool((working_after or {}).get("replace", False)),
                "seo_title": ((working_after or {}).get("seo") or {}).get("title", ""),
                "seo_desc": ((working_after or {}).get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
        "schema_ui_json": schema_ui_json,
        "error": None,
        "ok_message": "Changes saved.",
        "__entry_data_json": json.dumps(
            (_render_home_data(entry.data) if getattr(section, "key", "") == "home" else entry_json_for_client) or {},
            ensure_ascii=False
        ),
    },
)

    incoming_has_sections_key = "sections" in payload
    incoming_sections = payload.get("sections", None)
    replace_flag = bool(payload.get("replace", False))
    if incoming_has_sections_key and isinstance(incoming_sections, list) and len(incoming_sections) == 0 and not replace_flag:
        # Build UI list from either sections[] or object-style
        sections_ui = [{
            "index": i,
            "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
            "sec": (blk or {}),
        } for i, blk in enumerate(base_data.get("sections") or [])] or build_sections_ui_fallback_for_object_page(base_data)

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (base_data.get("title") or entry.slug),
                "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": json.dumps(payload, ensure_ascii=False, indent=2),
                "replace_val": replace_flag,
                "seo_title": (payload.get("seo") or {}).get("title", (base_data.get("seo") or {}).get("title", "")),
                "seo_desc": (payload.get("seo") or {}).get("description", (base_data.get("seo") or {}).get("description", "")),
                "sections_ui": sections_ui,
                "schema_ui_json": schema_ui_json,
                "error": "Cannot clear sections without replace=true.",
                "ok_message": None,
            },
            status_code=400,
        )

    # Non-destructive merge (draft-aware)
    is_published_now = (getattr(entry, "status", "draft") == "published")

    def _unwrap_draft(d: Any) -> Any:
        cur = d
        while isinstance(cur, dict) and "__draft" in cur:
            nxt = cur.get("__draft")
            if not isinstance(nxt, dict):
                break
            cur = nxt
        return cur

    working_base = _unwrap_draft(base_data.get("__draft")) if (is_published_now and isinstance(base_data.get("__draft"), dict)) else _unwrap_draft(base_data)
    if getattr(section, "key", "") == "home":
        # Home: use merged view (draft over root) so featuredProjects don't vanish
        working_base = _render_home_data(base_data)
    if isinstance(working_base, dict) and "__draft" in working_base:
        working_base = {k: v for k, v in working_base.items() if k != "__draft"}
    # Home: prevent wiping featuredProjects when the client sends empty/missing
    if getattr(section, "key", "") == "home":
        try:
            existing_fp = working_base.get("featuredProjects") if isinstance(working_base, dict) else []
        except Exception:
            existing_fp = []
        incoming_fp = payload.get("featuredProjects") if isinstance(payload, dict) else None
        if (not incoming_fp) and isinstance(existing_fp, list) and len(existing_fp) > 0:
            if isinstance(payload, dict):
                payload["featuredProjects"] = existing_fp

    merged = _deep_merge(working_base, payload)
    if not incoming_has_sections_key and "sections" in working_base:
        merged["sections"] = working_base["sections"]
    if isinstance(merged, dict) and "__draft" in merged:
        merged.pop("__draft", None)

    # Persist (draft vs root)
    is_published_now = (getattr(entry, "status", "draft") == "published")
    if getattr(section, "key", "") == "projects":
        # Projects: if published, stash into __draft so delivery stays stable until publish
        if is_published_now:
            base_clean = dict(base_data) if isinstance(base_data, dict) else {}
            base_clean.pop("__draft", None)
            base_clean["__draft"] = merged
            entry.data = base_clean
        else:
            entry.data = merged
    elif getattr(section, "key", "") == "privacy_policy":
        # Privacy Policy: accept both {body:...} and {privacy_policy:{body:...}}
        incoming = payload
        if isinstance(payload, dict) and "privacy_policy" in payload:
            pp = payload.get("privacy_policy")
            if isinstance(pp, dict):
                incoming = {**payload, **pp}
            elif isinstance(pp, str):
                incoming = {**payload, "body": pp}
        merged = _normalize_privacy_payload(incoming, base_data if isinstance(base_data, dict) else {})
        if is_published_now:
            # If already published, stash edits in __draft so delivery stays unchanged until publish
            base_clean = dict(base_data) if isinstance(base_data, dict) else {}
            base_clean.pop("__draft", None)
            base_clean["__draft"] = merged
            entry.data = base_clean
        else:
            entry.data = merged
    elif is_published_now:
        base_clean = dict(base_data)
        base_clean.pop("__draft", None)
        base_clean["__draft"] = merged
        entry.data = base_clean
    else:
        entry.data = merged

    entry.schema_version = active_version
    entry.updated_at = datetime.now(timezone.utc)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # Rebuild UI bits after save (based on current working data)
    current_base = entry.data or {}
    working_after = (current_base.get("__draft") if is_published_now and isinstance(current_base.get("__draft"), dict) else current_base)

    if getattr(section, "key", "") == "privacy_policy":
        sections_ui = [{
            "index": 0,
            "label": "01 - Privacy Policy",
            "sec": {"body": working_after.get("body", "")},
            "key": "privacy_policy",
        }]
    elif getattr(section, "key", "") == "home":
        wa = _render_home_data(entry.data)
        working_after = wa  # ensure initial_json and SEO values reflect merged view
        sections_ui = build_sections_ui_fallback_for_object_page(wa)
    elif getattr(section, "key", "") == "projects":
        sections_ui = [{
            "index": 0,
            "label": "01 - Projects",
            "sec": {"projects": (working_after or {}).get("projects", [])},
            "key": "projects",
        }]
    else:
        sections = (working_after.get("sections") or [])
        sections_ui = [{
            "index": i,
            "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
            "sec": (blk or {}),
        } for i, blk in enumerate(sections)] or build_sections_ui_fallback_for_object_page(working_after)

    if getattr(section, "key", "") == "home":
        entry_json_for_client = _render_home_data(entry.data)
    elif getattr(section, "key", "") == "projects":
        entry_json_for_client = _render_projects_data(entry.data)
    else:
        entry_json_for_client = working_after if working_after is not None else (entry.data or {})

    return templates.TemplateResponse(
        "admin/page_edit.html",
        {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (working_after or {}).get("title") or entry.slug,
                "status": entry.status,
                "section_name": section.name,
                "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                "section_id": int(section.id),
                "schema_version": active_version,
            },
            "initial_json": json.dumps(working_after or {}, ensure_ascii=False, indent=2),
            "replace_val": bool((working_after or {}).get("replace", False)),
            "seo_title": ((working_after or {}).get("seo") or {}).get("title", ""),
            "seo_desc": ((working_after or {}).get("seo") or {}).get("description", ""),
            "sections_ui": sections_ui,
            "schema_ui_json": schema_ui_json,
            "error": None,
            "ok_message": "Changes saved.",
            "__entry_data_json": json.dumps(entry_json_for_client or {}, ensure_ascii=False),
        },
    )


# --------------------------- Admin Publish proxy (session-based) ---------------------------
@router.post("/admin/pages/{entry_id}/publish")
def admin_publish_page(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Publish page: if data.__draft exists, promote it to root; otherwise publish current root.
    Delivery always reads entry.data (without __draft).
    """
    _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        raise HTTPException(status_code=400, detail="No active project.")

    entry, section = _load_entry_or_404(db, entry_id, tid)

    data_now = entry.data or {}
    working = data_now.get("__draft") if isinstance(data_now.get("__draft"), dict) else None
    candidate = (working or data_now)

    # Projects: publish merged view (root + draft) so we don't lose published items
    if getattr(section, "key", "") == "projects":
        candidate = _render_projects_data(data_now)
    # Home: publish merged view to keep featured projects and other blocks visible
    elif getattr(section, "key", "") == "home":
        candidate = _render_home_data(data_now)

    # Allow publish if either sections[] has content OR object-style has meaningful blocks
    has_sections = isinstance(candidate.get("sections"), list) and len(candidate["sections"]) > 0
    object_keys = [k for k in candidate.keys() if k not in ("seo", "replace", "__draft")]
    has_object_blocks = any(isinstance(candidate.get(k), dict) for k in object_keys)
    has_array_blocks = any(isinstance(candidate.get(k), list) and len(candidate.get(k) or []) > 0 for k in object_keys)
    has_primitive_content = any(
        isinstance(candidate.get(k), (str, int, float, bool))
        for k in candidate.keys()
        if k not in ("seo", "replace", "__draft")
    )
    if not (has_sections or has_object_blocks or has_array_blocks or has_primitive_content):
        raise HTTPException(status_code=409, detail="Cannot publish an empty page. Save content first.")

    now = datetime.now(timezone.utc)

    # If draft exists, promote it and clear __draft
    if working is not None:
        published_at_prev = getattr(entry, "published_at", None)
        data_new = dict(candidate)
        data_new.pop("__draft", None)
        entry.data = data_new
        if published_at_prev:
            try:
                setattr(entry, "published_at", published_at_prev)
            except Exception:
                pass

    # Publish
    entry.status = "published"
    try:
        setattr(entry, "published_at", now if not getattr(entry, "published_at", None) else getattr(entry, "published_at"))
    except Exception:
        pass
    entry.updated_at = now

    db.add(entry)
    db.commit()
    db.refresh(entry)

    return JSONResponse({"ok": True, "status": "published", "entry_id": int(entry.id)})


# --------------------------- Sections JSON (for Admin UI helpers) ---------------------------
@router.get("/admin/tenants/{tenant_id}/sections.json")
def sections_json(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Minimal JSON list of sections for a tenant. Used by admin UI dropdowns/filters.
    """
    _require_web_user(request)

    rows = db.execute(
        select(Section.id, Section.key, Section.name)
        .where(Section.tenant_id == tenant_id)
        .order_by(Section.name.asc())
    ).all()

    data = [{"id": int(i), "key": k, "name": n} for (i, k, n) in rows]
    return JSONResponse({"sections": data})
