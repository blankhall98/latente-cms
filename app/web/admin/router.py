# app/web/admin/router.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Union

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_, or_, desc
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema
from app.services.ui_schema_service import build_ui_contract
from app.security.preview_tokens import create_preview_token as issue_preview_token
from app.web.ui.preview_renderer import build_render_model

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


# =========== Path utils: dicts y listas ===========
def _is_int_token(tok: Union[str, int]) -> bool:
    if isinstance(tok, int):
        return True
    if isinstance(tok, str) and tok.isdigit():
        return True
    return False

def _tok_to_index(tok: Union[str, int]) -> int:
    return tok if isinstance(tok, int) else int(tok)

def _value_from_path(data: Any, path: List[Union[str, int]]):
    cur = data
    for p in path or []:
        if isinstance(cur, dict) and isinstance(p, str) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and _is_int_token(p):
            idx = _tok_to_index(p)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            return None
    return cur

def _ensure_for_set(container: Any, key: Union[str, int]):
    if isinstance(container, dict) and isinstance(key, str):
        if key not in container or not isinstance(container[key], (dict, list)):
            container[key] = {}
        return container[key]
    if isinstance(container, list) and isinstance(key, int):
        while len(container) <= key:
            container.append({})
        if not isinstance(container[key], (dict, list)):
            container[key] = {}
        return container[key]
    return None

def _set_value_at_path(dst: Any, path: List[Union[str, int]], value):
    if not path:
        return
    cur = dst
    for i, p in enumerate(path[:-1]):
        if isinstance(cur, dict) and isinstance(p, str):
            if p not in cur or not isinstance(cur[p], (dict, list)):
                nxt = path[i + 1] if i + 1 < len(path) else None
                cur[p] = [] if _is_int_token(nxt) else {}
            cur = cur[p]
        elif isinstance(cur, list) and _is_int_token(p):
            idx = _tok_to_index(p)
            while len(cur) <= idx:
                cur.append({})
            if not isinstance(cur[idx], (dict, list)):
                cur[idx] = {}
            cur = cur[idx]
        else:
            return
    last = path[-1]
    if isinstance(cur, dict) and isinstance(last, str):
        cur[last] = value
    elif isinstance(cur, list) and _is_int_token(last):
        idx = _tok_to_index(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value


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
        allowed = user.get("is_superadmin") or any(
            tt["id"] == int(current_tenant_id) for tt in user.get("tenants", [])
        )
        if t and allowed:
            current_tenant = t
        else:
            request.session.pop("current_tenant_id", None)

    sections: List[Section] = []
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
# UTIL: acceso/guard
# ============================================================
def _require_tenant_access(db: Session, user: dict, tenant_id: int) -> Tenant:
    t = _get_tenant(db, tenant_id)
    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in user.get("tenants", [])} if user.get("tenants") else set()
        if t.id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso a este tenant")
    return t


# ============================================================
# TENANTS (Proyectos) — list UI + sections.json util
# ============================================================
@admin_router.get("/tenants", response_class=HTMLResponse)
def tenants_list(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    msg: Optional[str] = Query(None),
    err: Optional[str] = Query(None),
) -> HTMLResponse:
    tenants_ctx = _tenants_for_context(db, user)
    return templates.TemplateResponse(
        "admin/tenants_list.html",
        {
            "request": request,
            "user": user,
            "tenants_ctx": tenants_ctx,
            "msg": msg,
            "err": err,
        },
    )


@admin_router.get("/tenants/{tenant_id}/sections.json")
def tenant_sections_json(
    tenant_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    tenant = _require_tenant_access(db, user, int(tenant_id))
    sections = db.execute(
        select(Section).where(Section.tenant_id == tenant.id).order_by(Section.id.asc())
    ).scalars().all()
    return JSONResponse([{"id": s.id, "key": s.key, "name": s.name} for s in sections])


# ============================================================
# SECTIONS — CRUD (UI) + Deep links a entries
# ============================================================
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


@admin_router.get("/sections/{section_id}/entries")
@admin_router.get("/sections/{section_id}/open")
def section_open_entries(
    section_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    _require_tenant_access(db, user, section.tenant_id)
    # Deep-link a entries ya filtrado
    return RedirectResponse(
        url=f"/admin/entries?tenant_id={section.tenant_id}&section_id={section.id}",
        status_code=status.HTTP_302_FOUND,
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

    exists = db.scalar(
        select(func.count()).select_from(Section).where(
            and_(Section.tenant_id == tenant.id, Section.key == key)
        )
    )
    if exists:
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
# ENTRIES (list & búsqueda q) — con autoredirect si hay 1 sección
# ============================================================
@admin_router.get("/entries", response_class=HTMLResponse)
def entries_list(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    tenant_id: Optional[int] = Query(None),
    section_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
) -> HTMLResponse:
    tenants_ctx = _tenants_for_context(db, user)
    current_tenant = None

    # Resolver tenant
    if tenant_id is not None:
        current_tenant = _require_tenant_access(db, user, int(tenant_id))
        request.session["current_tenant_id"] = int(tenant_id)
    elif request.session.get("current_tenant_id"):
        current_tenant = _require_tenant_access(db, user, int(request.session["current_tenant_id"]))

    sections_for_tenant: List[Section] = []
    selected_section: Optional[Section] = None
    entries: List[Entry] = []

    if current_tenant:
        sections_for_tenant = db.execute(
            select(Section).where(Section.tenant_id == current_tenant.id).order_by(Section.id.asc())
        ).scalars().all()

        # Si no llega section_id y hay exactamente 1 sección → UX autoredirect
        if (section_id is None) and len(sections_for_tenant) == 1:
            only = sections_for_tenant[0]
            return RedirectResponse(
                url=f"/admin/entries?tenant_id={current_tenant.id}&section_id={only.id}",
                status_code=status.HTTP_302_FOUND,
            )

        # Resolver selected_section de forma robusta
        if section_id is not None:
            try:
                sid = int(section_id)
                selected_section = next((s for s in sections_for_tenant if s.id == sid), None)
            except Exception:
                selected_section = None

        # Query de entries
        stmt = select(Entry).where(Entry.tenant_id == current_tenant.id)
        if selected_section:
            stmt = stmt.where(Entry.section_id == selected_section.id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Entry.slug.ilike(like)))
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
            "q": q or "",
            "selected_section": selected_section,
        },
    )


# ---------- Helpers para UI/Schema ----------
def _active_schema(db: Session, tenant_id: int, section_id: int) -> Optional[SectionSchema]:
    # Preferir is_active=True, si no existe, tomar la versión más alta
    active = db.scalar(
        select(SectionSchema).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.is_active == True  # noqa: E712
        ).limit(1)
    )
    if active:
        return active
    return db.scalar(
        select(SectionSchema)
        .where(SectionSchema.tenant_id == tenant_id, SectionSchema.section_id == section_id)
        .order_by(desc(SectionSchema.version))
        .limit(1)
    )

def _active_schema_version(db: Session, tenant_id: int, section_id: int) -> Optional[int]:
    row = db.scalar(
        select(SectionSchema.version).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.is_active == True  # noqa: E712
        ).limit(1)
    )
    if row is not None:
        return int(row)
    # fallback al máximo version
    maxv = db.scalar(
        select(func.max(SectionSchema.version)).where(
            SectionSchema.tenant_id == tenant_id, SectionSchema.section_id == section_id
        )
    )
    return int(maxv) if maxv is not None else None


def _cast_value(schema_sub: dict, raw: str):
    t = (schema_sub or {}).get("type")
    if not schema_sub:
        return raw
    if "enum" in schema_sub:
        return raw
    if t == "boolean":
        return str(raw).lower() in ("true", "on", "1", "yes")
    if t in ("number", "integer"):
        try:
            return int(raw) if t == "integer" else float(raw)
        except Exception:
            return None
    if t == "array" and (schema_sub.get("items") or {}).get("type") == "string":
        return [s.strip() for s in str(raw).split(",") if s.strip()]
    return raw


def _schema_for_path(schema_dict: dict, path: List[Union[str, int]]) -> dict:
    cur = schema_dict or {}
    sub = {}
    for token in path:
        props = cur.get("properties", {}) if isinstance(cur, dict) else {}
        if isinstance(token, str):
            sub = props.get(token, {})
            cur = sub
        else:  # índice de array
            if (cur or {}).get("type") == "array":
                cur = (cur.get("items") or {})
                sub = cur
            else:
                sub = {}
                cur = {}
    return sub or {}


def _normalize_ui_hints(ui_hints: list[dict]) -> list[dict]:
    out: list[dict] = []
    for h in ui_hints or []:
        hh = dict(h)
        path = hh.get("path") or []
        path_str = [str(p) for p in path]
        hh["path"] = path_str
        if not hh.get("name"):
            hh["name"] = hh.get("label") or (path_str[-1] if path_str else "field")
        out.append(hh)
    return out


def _autogenerate_ui_hints_from_data(data: dict) -> list[dict]:
    hints: list[dict] = []
    sections = (data or {}).get("sections") or []
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        base = {"section": sec.get("type") or f"Section {i+1}"}
        candidates = [
            ("Eyebrow", ["sections", str(i), "eyebrow"]),
            ("Heading", ["sections", str(i), "heading"]),
            ("Subheading", ["sections", str(i), "subheading"]),
            ("Title", ["sections", str(i), "title"]),
            ("Intro", ["sections", str(i), "intro"]),
            ("Description", ["sections", str(i), "description"]),
            ("Body", ["sections", str(i), "richText"]),
            ("Data.Heading", ["sections", str(i), "data", "heading"]),
            ("Data.Subheading", ["sections", str(i), "data", "subheading"]),
            ("Data.Title", ["sections", str(i), "data", "title"]),
            ("Data.Intro", ["sections", str(i), "data", "intro"]),
            ("Data.Description", ["sections", str(i), "data", "description"]),
            ("Overlay.Text", ["sections", str(i), "data", "overlay", "text"]),
        ]
        for label, path in candidates:
            val = _value_from_path(data, path)
            if isinstance(val, (str, int, float)) and str(val).strip() != "":
                hints.append({"name": f"{base['section']} · {label}", "path": path, "widget": "text"})
        items_path = ["sections", str(i), "data", "items"]
        items = _value_from_path(data, items_path)
        if isinstance(items, list):
            for j, it in enumerate(items):
                if isinstance(it, dict):
                    if isinstance(it.get("title"), str):
                        hints.append({"name": f"{base['section']} · Item {j+1} · Title",
                                      "path": items_path + [str(j), "title"], "widget": "text"})
                    if isinstance(it.get("description"), str):
                        hints.append({"name": f"{base['section']} · Item {j+1} · Description",
                                      "path": items_path + [str(j), "description"], "widget": "textarea"})
    return hints


# ============================================================
# ENTRIES — Editor amigable por secciones (entry_edit.html)
# ============================================================
@admin_router.get("/entries/{entry_id}/edit", response_class=HTMLResponse)
def entry_edit_editor(
    request: Request,
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
) -> HTMLResponse:
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in (user.get("tenants") or [])}
        if entry.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")

    tenant = db.get(Tenant, entry.tenant_id)
    section = db.get(Section, entry.section_id)
    schema_active = _active_schema(db, entry.tenant_id, entry.section_id)
    active_version = _active_schema_version(db, entry.tenant_id, entry.section_id)

    contract: dict = {}
    ui_hints: list[dict] = []
    try:
        contract = build_ui_contract(db, tenant_id=entry.tenant_id, section_id=entry.section_id) or {}
        ui_hints = contract.get("ui_hints", []) or []
    except Exception:
        ui_hints = []

    try:
        render_model = build_render_model(ui_hints, entry.data or {})
        preview_sections_order = [s.get("title", "General") for s in (render_model.get("sections") or [])]
    except Exception:
        render_model = {}
        preview_sections_order = []

    sections_payload = []
    try:
        sections_payload = list((entry.data or {}).get("sections", []))
    except Exception:
        sections_payload = []

    values_map: Dict[str, Any] = {}
    if entry.data and ui_hints:
        for f in ui_hints:
            name = f.get("name")
            path = f.get("path") or []
            if name:
                values_map[name] = _value_from_path(entry.data, path)

    return templates.TemplateResponse(
        "admin/entry_edit.html",
        {
            "request": request,
            "user": user,
            "entry": entry,
            "tenant": tenant,
            "section": section,
            "schema_active": schema_active,
            "active_schema_version": active_version,
            "ui_hints": ui_hints,
            "values_map": values_map,
            "schema_dict": (schema_active.schema if schema_active else {}) or {},
            "preview_sections_order": preview_sections_order,
            "sections_payload": sections_payload,
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err"),
        },
    )


@admin_router.post("/entries/{entry_id}/update")
async def entry_update_editor(
    request: Request,
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in (user.get("tenants") or [])}
        if entry.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")

    form = await request.form()
    slug = (form.get("slug") or "").strip()
    status_val = (form.get("status") or entry.status).strip()
    sv = form.get("schema_version")
    data_json_fallback = form.get("data_json") or ""

    if not slug:
        return RedirectResponse(
            url=f"/admin/entries/{entry_id}/edit?err=Slug requerido",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    ss = _active_schema(db, entry.tenant_id, entry.section_id)
    schema_dict = (ss.schema or {}) if ss else {}

    new_data: Dict[str, Any] = entry.data.copy() if isinstance(entry.data, dict) else {}

    # json_section_<idx> → data.sections[idx].data
    sections_list = list(new_data.get("sections", [])) if isinstance(new_data.get("sections"), list) else []
    for k, v in form.items():
        if not str(k).startswith("json_section_"):
            continue
        idx_str = str(k).split("json_section_")[-1].strip()
        if not idx_str.isdigit():
            continue
        idx = int(idx_str)
        try:
            block_data = json.loads(v) if (v or "").strip() else {}
            if not isinstance(block_data, (dict, list)):
                raise ValueError("El JSON debe ser objeto o arreglo")
        except Exception as e:
            return RedirectResponse(
                url=f"/admin/entries/{entry_id}/edit?err=JSON inválido en sección #{idx+1}: {e}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        while len(sections_list) <= idx:
            sections_list.append({"type": "Section", "data": {}})
        if not isinstance(sections_list[idx], dict):
            sections_list[idx] = {"type": "Section", "data": {}}
        sections_list[idx]["data"] = block_data
    if sections_list:
        new_data["sections"] = sections_list

    # json__path → objeto
    for k, v in form.items():
        if not str(k).startswith("json__"):
            continue
        path = str(k)[6:].split("__")
        try:
            value = json.loads(v) if (v or "").strip() else None
        except Exception as e:
            return RedirectResponse(
                url=f"/admin/entries/{entry_id}/edit?err=JSON inválido en {'.'.join(path)}: {e}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if value is not None:
            _set_value_at_path(new_data, path, value)

    # f__path → tipos simples
    has_f = any(str(k).startswith("f__") for k in form.keys())
    if has_f:
        for k, v in form.items():
            if not str(k).startswith("f__"):
                continue
            path = str(k)[3:].split("__")
            subschema = _schema_for_path(schema_dict, path) if schema_dict else {}
            casted = _cast_value(subschema, v)
            _set_value_at_path(new_data, path, casted)

    # Fallback: JSON global
    elif data_json_fallback and not sections_list:
        try:
            data_obj = json.loads(data_json_fallback)
            if not isinstance(data_obj, dict):
                raise ValueError("El JSON de datos debe ser un objeto")
            new_data = data_obj
        except Exception as e:
            return RedirectResponse(
                url=f"/admin/entries/{entry_id}/edit?err=JSON inválido: {e}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    entry.data = new_data
    entry.slug = slug

    try:
        if sv is not None:
            entry.schema_version = int(sv)
    except Exception:
        pass

    if status_val in ("draft", "published", "archived"):
        entry.status = status_val

    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(
        url=f"/admin/entries/{entry_id}/edit?msg=Cambios guardados",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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
        allowed_ids = {tt["id"] for tt in (user.get("tenants") or [])}
        if entry.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")

    entry.status = "published"
    entry.published_at = datetime.now(timezone.utc)
    entry.archived_at = None
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(
        url=f"/admin/entries/{entry_id}/edit?msg=Publicado",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@admin_router.post("/entries/{entry_id}/unpublish")
def entry_unpublish(
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="Entry not found")
    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in (user.get("tenants") or [])}
        if e.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")
    e.status = "draft"
    e.archived_at = None
    e.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(
        url=f"/admin/entries/{entry_id}/edit?msg=Despublicado",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@admin_router.post("/entries/{entry_id}/archive")
def entry_archive(
    entry_id: int,
    db: Session = Depends(get_db),
    user = Depends(current_session),
):
    e = db.get(Entry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="Entry not found")
    if not user.get("is_superadmin"):
        allowed_ids = {tt["id"] for tt in (user.get("tenants") or [])}
        if e.tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso")
    e.status = "archived"
    e.archived_at = datetime.now(timezone.utc)
    e.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(
        url=f"/admin/entries/{entry_id}/edit?msg=Archivado",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ============================================================
# PREVIEW (privado)
# ============================================================
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
        allowed_ids = {tt["id"] for tt in (user.get("tenants") or [])}
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









