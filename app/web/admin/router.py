# app/web/admin/router.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema
from app.services.ui_schema_service import build_ui_contract
from app.security.preview_tokens import create_preview_token as issue_preview_token
from app.web.ui.preview_renderer import build_render_model  # 👈

templates = Jinja2Templates(directory="app/templates")
admin_router = APIRouter(prefix="/admin", tags=["admin"])

# ---------- Sesión ----------
def current_session(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login requerido")
    return user

def _get_tenant(db: Session, tenant_id: int) -> Tenant:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t

def _tenants_for_context(db: Session, user: dict) -> list[dict]:
    """
    Devuelve [{id, slug, name}] para la barra/lateral.
    user["tenants"] viene como lista de dicts (id, slug, name).
    """
    if user.get("is_superadmin"):
        rows = db.execute(select(Tenant).order_by(Tenant.id.asc())).scalars().all()
        return [{"id": t.id, "slug": t.slug, "name": t.name} for t in rows]
    else:
        allowed_ids = {t["id"] for t in user.get("tenants", [])} if user.get("tenants") else set()
        rows = []
        if allowed_ids:
            rows = db.execute(
                select(Tenant).where(Tenant.id.in_(allowed_ids)).order_by(Tenant.id.asc())
            ).scalars().all()
        return [{"id": t.id, "slug": t.slug, "name": t.name} for t in rows]

def _value_from_path(data: dict, path: list[str]):
    cur = data
    for p in path or []:
        if isinstance(cur, dict) and (p in cur):
            cur = cur[p]
        else:
            return None
    return cur

# ============================================================
# DASHBOARD
# ============================================================
@admin_router.get("/", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
) -> HTMLResponse:
    tenants_ctx = _tenants_for_context(db, user)

    current_tenant = None
    current_tenant_id = request.session.get("current_tenant_id")
    if current_tenant_id:
        t = db.get(Tenant, int(current_tenant_id))
        allowed = user.get("is_superadmin") or any(tt["id"] == int(current_tenant_id) for tt in user.get("tenants", []))
        if t and allowed:
            current_tenant = t
        else:
            request.session.pop("current_tenant_id", None)

    sections = []
    published_count = 0
    recent_activity = []

    if current_tenant:
        sections = db.execute(
            select(Section)
            .where(Section.tenant_id == current_tenant.id)
            .order_by(Section.id.asc())
        ).scalars().all()

        published_count = db.scalar(
            select(func.count())
            .select_from(Entry)
            .where(Entry.tenant_id == current_tenant.id, Entry.status == "published")
        ) or 0

        last_entries = db.execute(
            select(Entry)
            .where(Entry.tenant_id == current_tenant.id)
            .order_by(Entry.updated_at.desc())
            .limit(10)
        ).scalars().all()

        recent_activity = [
            {
                "when": e.updated_at.strftime("%Y-%m-%d %H:%M") if e.updated_at else "",
                "section_key": e.section.key if hasattr(e, "section") and e.section else str(e.section_id),
                "slug": e.slug,
                "status": e.status,
                "entry_id": e.id,
                "tenant_id": e.tenant_id,
                "tenant_slug": current_tenant.slug if current_tenant else None,
            }
            for e in last_entries
        ]

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "tenants_ctx": tenants_ctx,
            "current_tenant": current_tenant,
            "sections": sections,
            "published_count": published_count,
            "recent_activity": recent_activity,
        },
    )

@admin_router.post("/switch-tenant")
def switch_tenant(
    request: Request,
    tenant_id: int = Form(...),
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in user.get("tenants", [])} if user.get("tenants") else set()
        if tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso a este tenant")

    request.session["current_tenant_id"] = int(tenant_id)
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

# ============================================================
# SECTIONS — CRUD (UI)
# ============================================================
def _require_tenant_access(db: Session, user: dict, tenant_id: int) -> Tenant:
    t = _get_tenant(db, tenant_id)
    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in user.get("tenants", [])} if user.get("tenants") else set()
        if t.id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso a este tenant")
    return t

@admin_router.get("/sections", response_class=HTMLResponse)
def sections_list(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    tenant_id: Optional[int] = Query(None),
    msg: Optional[str] = Query(None),
    err: Optional[str] = Query(None),
) -> HTMLResponse:
    current_tenant = None
    if tenant_id:
        current_tenant = _require_tenant_access(db, user, int(tenant_id))
        request.session["current_tenant_id"] = int(tenant_id)
    elif request.session.get("current_tenant_id"):
        current_tenant = _require_tenant_access(db, user, int(request.session["current_tenant_id"]))

    if not current_tenant:
        return templates.TemplateResponse(
            "admin/sections_list.html",
            {
                "request": request,
                "user": user,
                "current_tenant": None,
                "sections": [],
                "msg": None,
                "err": "Selecciona un proyecto para ver sus secciones.",
            },
        )

    sections = db.execute(
        select(Section).where(Section.tenant_id == current_tenant.id).order_by(Section.id.asc())
    ).scalars().all()

    return templates.TemplateResponse(
        "admin/sections_list.html",
        {
            "request": request,
            "user": user,
            "current_tenant": current_tenant,
            "sections": sections,
            "msg": msg,
            "err": err,
        },
    )

@admin_router.get("/sections/new", response_class=HTMLResponse)
def section_new(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    tenant_id: Optional[int] = Query(None),
) -> HTMLResponse:
    current_tenant = None
    if tenant_id:
        current_tenant = _require_tenant_access(db, user, int(tenant_id))
        request.session["current_tenant_id"] = int(tenant_id)
    elif request.session.get("current_tenant_id"):
        current_tenant = _require_tenant_access(db, user, int(request.session["current_tenant_id"]))
    else:
        raise HTTPException(status_code=400, detail="tenant_id requerido")

    return templates.TemplateResponse(
        "admin/section_form.html",
        {
            "request": request,
            "user": user,
            "tenant": current_tenant,
            "section": None,
            "mode": "create",
            "err": None,
        },
    )

@admin_router.post("/sections/create")
def section_create(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    tenant_id: int = Form(...),
    name: str = Form(...),
    key: str = Form(...),
    description: Optional[str] = Form(None),
):
    tenant = _require_tenant_access(db, user, int(tenant_id))

    key = (key or "").strip()
    name = (name or "").strip()
    description = (description or "") or None

    if not key or not name:
        return RedirectResponse(
            url=f"/admin/sections/new?tenant_id={tenant.id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Unicidad por tenant
    exists = db.scalar(
        select(func.count()).select_from(Section).where(
            and_(Section.tenant_id == tenant.id, Section.key == key)
        )
    )
    if exists:
        # Re-renderizamos con error
        return templates.TemplateResponse(
            "admin/section_form.html",
            {
                "request": request,
                "user": user,
                "tenant": tenant,
                "section": {"name": name, "key": key, "description": description},
                "mode": "create",
                "err": "La clave ya existe en este proyecto.",
            },
            status_code=400,
        )

    s = Section(tenant_id=tenant.id, key=key, name=name, description=description)
    db.add(s)
    db.commit()
    return RedirectResponse(
        url=f"/admin/sections?tenant_id={tenant.id}&msg=Sección creada",
        status_code=status.HTTP_303_SEE_OTHER,
    )

@admin_router.get("/sections/{section_id}/edit", response_class=HTMLResponse)
def section_edit(
    request: Request,
    section_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
) -> HTMLResponse:
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    tenant = _require_tenant_access(db, user, section.tenant_id)
    request.session["current_tenant_id"] = tenant.id

    return templates.TemplateResponse(
        "admin/section_form.html",
        {
            "request": request,
            "user": user,
            "tenant": tenant,
            "section": section,
            "mode": "edit",
            "err": None,
        },
    )

@admin_router.post("/sections/{section_id}/update")
def section_update(
    section_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    name: str = Form(...),
    description: Optional[str] = Form(None),
):
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    tenant = _require_tenant_access(db, user, section.tenant_id)
    section.name = (name or "").strip()
    section.description = (description or "") or None
    section.updated_at = datetime.now(timezone.utc)
    db.commit()

    return RedirectResponse(
        url=f"/admin/sections?tenant_id={tenant.id}&msg=Sección actualizada",
        status_code=status.HTTP_303_SEE_OTHER,
    )

@admin_router.post("/sections/{section_id}/delete")
def section_delete(
    section_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    tenant = _require_tenant_access(db, user, section.tenant_id)

    # Seguridad: impedir borrar si tiene entries
    entries_count = db.scalar(
        select(func.count()).select_from(Entry).where(
            and_(Entry.tenant_id == tenant.id, Entry.section_id == section.id)
        )
    ) or 0
    if entries_count > 0:
        return RedirectResponse(
            url=f"/admin/sections?tenant_id={tenant.id}&err=No se puede eliminar: hay {entries_count} entries en esta sección",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    db.delete(section)
    db.commit()
    return RedirectResponse(
        url=f"/admin/sections?tenant_id={tenant.id}&msg=Sección eliminada",
        status_code=status.HTTP_303_SEE_OTHER,
    )

# ============================================================
# ENTRIES (list & minimal)
# ============================================================
@admin_router.get("/entries", response_class=HTMLResponse)
def entries_list(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    tenant_id: Optional[int] = Query(None),
    section_id: Optional[int] = Query(None),
) -> HTMLResponse:
    tenants_ctx = _tenants_for_context(db, user)
    current_tenant = None
    if tenant_id:
        current_tenant = _get_tenant(db, tenant_id)
        request.session["current_tenant_id"] = int(tenant_id)
    elif request.session.get("current_tenant_id"):
        current_tenant = _get_tenant(db, int(request.session["current_tenant_id"]))

    sections_for_tenant = []
    entries = []
    if current_tenant:
        sections_for_tenant = db.execute(
            select(Section).where(Section.tenant_id == current_tenant.id).order_by(Section.id.asc())
        ).scalars().all()

        stmt = select(Entry).where(Entry.tenant_id == current_tenant.id)
        if section_id:
            stmt = stmt.where(Entry.section_id == section_id)
        entries = db.execute(stmt.order_by(Entry.updated_at.desc())).scalars().all()

    return templates.TemplateResponse(
        "admin/entries_list.html",
        {
            "request": request,
            "user": user,
            "tenants_ctx": tenants_ctx,
            "current_tenant": current_tenant,
            "sections_for_tenant": sections_for_tenant,
            "entries": entries,
        },
    )

# ---------- Autoform helpers ----------
def _active_schema(db: Session, tenant_id: int, section_id: int) -> Optional[SectionSchema]:
    return db.scalar(
        select(SectionSchema).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.is_active == True  # noqa: E712
        ).limit(1)
    )

def _cast_value(schema_sub: dict, raw: str):
    t = schema_sub.get("type")
    if "enum" in schema_sub:
        return raw
    if t == "boolean":
        return str(raw).lower() in ("true", "on", "1", "yes")
    if t in ("number", "integer"):
        try:
            return int(raw) if t == "integer" else float(raw)
        except Exception:
            return None
    if t == "array" and schema_sub.get("items", {}).get("type") == "string":
        return [s.strip() for s in str(raw).split(",") if s.strip()]
    return raw

def _schema_for_path(schema_dict: dict, path: list[str]) -> dict:
    cur = schema_dict.get("properties", {})
    sub = {}
    for key in path:
        sub = cur.get(key, {})
        if sub.get("type") == "object":
            cur = sub.get("properties", {})
        else:
            cur = {}
    return sub or {}

def _set_value_at_path(dst: dict, path: list[str], value):
    cur = dst
    for key in path[:-1]:
        if key not in cur or not isinstance(cur[key], dict):
            cur[key] = {}
        cur = cur[key]
    cur[path[-1]] = value

# ---------- Autoform UI ----------
@admin_router.get("/entries/{entry_id}/edit", response_class=HTMLResponse)
def entry_edit_autoform(
    request: Request,
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
) -> HTMLResponse:
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in user.get("tenants", [])} if user.get("tenants") else set()
        if entry.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")

    contract = {}
    ui_hints: list[dict] = []
    schema_version = entry.schema_version
    try:
        contract = build_ui_contract(db, tenant_id=entry.tenant_id, section_id=entry.section_id)
        ui_hints = contract.get("ui_hints", []) or []
        schema_version = contract.get("version", entry.schema_version)
    except Exception:
        ui_hints = []
        schema_version = entry.schema_version

    values_map: Dict[str, Any] = {}
    if entry.data and ui_hints:
        for f in ui_hints:
            name = f.get("name")
            path = f.get("path") or []
            if name:
                values_map[name] = _value_from_path(entry.data, path)

    return templates.TemplateResponse(
        "admin/entry_autoform.html",
        {
            "request": request,
            "user": user,
            "entry": entry,
            "schema_version": schema_version,
            "ui_hints": ui_hints,
            "tenant_id": entry.tenant_id,
            "section_id": entry.section_id,
            "values_map": values_map,
        },
    )

@admin_router.post("/entries/{entry_id}/save")
async def entry_save_autoform(
    request: Request,
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in user.get("tenants", [])} if user.get("tenants") else set()
        if entry.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")

    form = await request.form()
    new_slug = (form.get("slug") or "").strip()
    new_schema_version = form.get("schema_version")
    if not new_slug:
        raise HTTPException(status_code=400, detail="Slug requerido")

    ss = _active_schema(db, entry.tenant_id, entry.section_id)
    schema_dict = (ss.schema or {}) if ss else {}

    if new_schema_version:
        try:
            entry.schema_version = int(new_schema_version)
        except Exception:
            pass

    new_data: Dict[str, Any] = {}
    for k, v in form.items():
        if not k.startswith("f__"):
            continue
        path = k[3:].split("__")
        subschema = _schema_for_path(schema_dict, path) if schema_dict else {}
        casted = _cast_value(subschema, v)
        _set_value_at_path(new_data, path, casted)

    entry.slug = new_slug
    if new_data:
        entry.data = new_data
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url=f"/admin/entries/{entry_id}/edit", status_code=status.HTTP_303_SEE_OTHER)

@admin_router.post("/entries/{entry_id}/publish")
def entry_publish(
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in user.get("tenants", [])} if user.get("tenants") else set()
        if entry.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")

    entry.status = "published"
    entry.published_at = datetime.now(timezone.utc)
    entry.archived_at = None
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url=f"/admin/entries/{entry_id}/edit", status_code=status.HTTP_303_SEE_OTHER)

@admin_router.get("/entries/{entry_id}/preview", response_class=HTMLResponse)
def entry_preview(
    request: Request,
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
) -> HTMLResponse:
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in user.get("tenants", [])} if user.get("tenants") else set()
        if entry.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")

    section = db.get(Section, entry.section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    tenant = db.get(Tenant, entry.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        contract = build_ui_contract(db, tenant_id=entry.tenant_id, section_id=entry.section_id)
        ui_hints = contract.get("ui_hints", []) if isinstance(contract, dict) else []
    except Exception:
        ui_hints = []

    render_model = build_render_model(ui_hints, entry.data or {})

    token = issue_preview_token(
        tenant_id=entry.tenant_id,
        entry_id=entry.id,
        schema_version=entry.schema_version,
        expires_in=int(timedelta(minutes=15).total_seconds()),
    )

    public_preview_url = f"/preview/{token}"
    delivery_url = f"/delivery/v1/tenants/{tenant.slug}/sections/{section.key}/entries/{entry.slug}"

    return templates.TemplateResponse(
        "admin/entry_preview.html",
        {
            "request": request,
            "user": user,
            "entry": entry,
            "tenant": tenant,
            "section": section,
            "delivery_url": delivery_url,
            "public_preview_url": public_preview_url,
            "render_model": render_model,
            "preview_model": render_model,
        },
    )
