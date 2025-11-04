# app/web/admin/router.py
from __future__ import annotations

from typing import Any, Optional
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant, User, UserTenant, UserTenantStatus, Role
from app.models.content import Section, Entry
from app.services.passwords import verify_password

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


def _load_entry_or_404(db: Session, entry_id: int, tenant_id: int):
    row = db.execute(
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(and_(Entry.id == entry_id, Entry.tenant_id == tenant_id))
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Page not found in this project")
    return row  # (Entry, Section)


def _json_is_object(payload: object) -> bool:
    # For MVP we only allow a JSON object at the root (maps to form fields later)
    return isinstance(payload, dict)


def _validate_json_against_active_schema(entry: Entry, section: Section, data_obj: dict) -> list[str]:
    """
    Placeholder for Step 8E: run JSON Schema (Draft 2020-12) against the section's active schema.
    Return a list of human-readable error strings; empty list means valid.
    """
    # TODO(step 8E): use registry/SectionSchema active version + jsonschema validator
    return []


# ---------------------------
# Web Login (email/password)
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
# Dashboard (LIVE data — 8B)
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
        title = (e.data or {}).get("title") or e.slug or f"Page {getattr(e, 'id', '')}"
        recent_entries.append({
            "title": title,
            "sub": f"{section_key} / {tenant_slug} · {status_text}",
            "id": int(getattr(e, "id", 0)) if getattr(e, "id", None) else None,
        })

    quick_links = [
        {"href": "/admin/projects", "title": "Browse Projects", "sub": "Switch between your projects"},
        {"href": "/admin/pages", "title": "All Pages", "sub": "View and edit pages"},
        # MVP: no create link
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
# Pages list (live) — 8B
# ---------------------------
@router.get("/admin/pages")
def pages_list(
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: Optional[int] = Query(default=None),
):
    """List pages inside the active project with search/filters/pagination."""
    user = (request.session or {}).get(SESSION_USER_KEY)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    active = _get_active_tenant(request)
    tid = int(tenant_id or (active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    # Query params
    q: Optional[str] = request.query_params.get("q")
    status_param: Optional[str] = request.query_params.get("status")  # "published", "draft", "archived"
    section_id_param: Optional[str] = request.query_params.get("section")
    page = _parse_int(request.query_params.get("page"), 1)
    per_page = _parse_int(request.query_params.get("per_page"), 10)
    page = max(page, 1)
    per_page = max(min(per_page, 50), 5)
    offset = (page - 1) * per_page

    # Base query
    base = (
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(Entry.tenant_id == tid)
    )

    # Filter by status
    VALID_STATUS = {"published", "draft", "archived"}
    if status_param:
        s = status_param.strip().lower()
        if s in VALID_STATUS:
            base = base.where(Entry.status == s)

    # Filter by section
    if section_id_param:
        try:
            sid = int(section_id_param)
            base = base.where(Entry.section_id == sid)
        except Exception:
            pass

    # Search by slug or title (JSONB)
    if q:
        ilike_term = f"%{q.strip()}%"
        try:
            base = base.where(
                or_(
                    Entry.slug.ilike(ilike_term),
                    Entry.data["title"].astext.ilike(ilike_term),  # Postgres JSONB
                )
            )
        except Exception:
            base = base.where(Entry.slug.ilike(ilike_term))

    # Count (exact)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    # Order + page slice
    try:
        order_cols = [Entry.updated_at.desc().nullslast(), Entry.id.desc()]
    except Exception:
        order_cols = [Entry.id.desc()]
    rows = db.execute(base.order_by(*order_cols).limit(per_page).offset(offset)).all()

    # Sections dropdown
    sects = db.execute(
        select(Section.id, Section.name).where(Section.tenant_id == tid).order_by(Section.name.asc())
    ).all()

    # Build UI items
    items = []
    for e, s in rows:
        title = (e.data or {}).get("title") or e.slug
        items.append({
            "id": e.id,
            "title": title,
            "slug": e.slug,
            "section_name": s.name,
            "status": e.status,  # "published"/"draft"/"archived"
            "updated_at": e.updated_at,
        })

    # Pager flags
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


# ---------------------------
# Page detail (Sections shell) — 8C
# ---------------------------
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
                "schema_version": entry.schema_version,
            },
            "sections_nav": sections_nav,
            "current_tab": current_tab,
            "current_payload": current_payload,
        },
    )


# --------------------------- Page Editor (8D MVP) ---------------------------
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

    initial_json = json.dumps(entry.data or {}, ensure_ascii=False, indent=2)
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
                "schema_version": entry.schema_version,
            },
            "initial_json": initial_json,
            "error": None,
            "ok_message": None,
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
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    # Parse JSON
    try:
        parsed = json.loads(content_json)
    except json.JSONDecodeError as e:
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
                    "schema_version": entry.schema_version,
                },
                "initial_json": content_json,  # keep user input
                "error": f"Invalid JSON: {str(e)}",
                "ok_message": None,
            },
            status_code=400,
        )

    # Root must be an object for our schema-driven UI
    if not _json_is_object(parsed):
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
                    "schema_version": entry.schema_version,
                },
                "initial_json": content_json,
                "error": "Root must be a JSON object (e.g., { \"title\": \"...\" }).",
                "ok_message": None,
            },
            status_code=400,
        )

    # (8E) Schema validation hook
    schema_errors = _validate_json_against_active_schema(entry, section, parsed)
    if schema_errors:
        first_issues = "; ".join(schema_errors[:5])
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
                    "schema_version": entry.schema_version,
                },
                "initial_json": content_json,
                "error": f"Schema validation failed: {first_issues}",
                "ok_message": None,
            },
            status_code=422,
        )

    # Save
    entry.data = parsed
    entry.updated_at = datetime.now(timezone.utc)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    ok_msg = "Changes saved successfully."
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
                "schema_version": entry.schema_version,
            },
            "initial_json": json.dumps(entry.data or {}, ensure_ascii=False, indent=2),
            "error": None,
            "ok_message": ok_msg,
        },
    )




