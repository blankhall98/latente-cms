# app/web/admin/router.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Iterable

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant
from app.models.content import Entry, Section

templates = Jinja2Templates(directory="app/templates")
admin_router = APIRouter(prefix="/admin", tags=["admin"])

# ------------- Sesión mínima -------------
def current_session(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login requerido")
    return user

def _ids_from_session_tenants(tenants_field: Optional[Iterable]) -> set[int]:
    """
    Normaliza el campo 'tenants' de la sesión.
    Puede venir como lista de dicts ({id, slug, name}) o lista de ints.
    """
    if not tenants_field:
        return set()
    out: set[int] = set()
    for item in tenants_field:
        if isinstance(item, dict) and "id" in item:
            out.add(int(item["id"]))
        elif isinstance(item, int):
            out.add(int(item))
    return out

def _tenants_for_context(db: Session, user: dict) -> list[dict]:
    """
    Para la UI: objetos simples {id, slug, name}.
    Superadmin: todos; Usuario: solo sus tenants activos.
    """
    if user.get("is_superadmin"):
        rows = db.execute(select(Tenant.id, Tenant.slug, Tenant.name).order_by(Tenant.id.asc())).all()
        return [{"id": r.id, "slug": r.slug, "name": r.name} for r in rows]

    allowed_ids = _ids_from_session_tenants(user.get("tenants", []))
    if not allowed_ids:
        return []
    rows = db.execute(
        select(Tenant.id, Tenant.slug, Tenant.name)
        .where(Tenant.id.in_(allowed_ids))
        .order_by(Tenant.id.asc())
    ).all()
    return [{"id": r.id, "slug": r.slug, "name": r.name} for r in rows]

def _get_tenant(db: Session, tenant_id: int) -> Tenant:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t

# ------------- Dashboard -------------
@admin_router.get("/", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
) -> HTMLResponse:
    """
    Dashboard:
    - tenants_ctx: para superadmin = todos; usuario normal = solo sus tenants.
    - current_tenant: desde sesión si existe y es accesible; si no, el primero disponible en contexto.
    - KPIs/actividad del tenant actual.
    """
    tenants_ctx = _tenants_for_context(db, user)

    # Resolver current_tenant
    current_tenant = None
    current_tenant_id = request.session.get("current_tenant_id")
    allowed_ids = {t["id"] for t in tenants_ctx} if tenants_ctx else set()

    if current_tenant_id and (int(current_tenant_id) in allowed_ids or user.get("is_superadmin")):
        current_tenant = _get_tenant(db, int(current_tenant_id))
    elif tenants_ctx:
        # Por usabilidad: si no hay seleccionado, usamos el primero disponible
        first_id = tenants_ctx[0]["id"]
        current_tenant = _get_tenant(db, first_id)
        request.session["current_tenant_id"] = int(first_id)

    # Datos del tenant actual
    sections = []
    published_count = 0
    recent_activity = []

    if current_tenant:
        sections = db.execute(
            select(Section).where(Section.tenant_id == current_tenant.id).order_by(Section.id.asc())
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
                "section_key": e.section.key if getattr(e, "section", None) else str(e.section_id),
                "slug": e.slug,
                "status": e.status,
                "entry_id": e.id,
                "tenant_id": e.tenant_id,
                "tenant_slug": current_tenant.slug,
            }
            for e in last_entries
        ]

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "tenants_ctx": tenants_ctx,       # KPI y lista “Tus proyectos”
            "current_tenant": current_tenant, # CTA y secciones
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
    """
    Fija el tenant actual en sesión.
    Superadmin: cualquiera; Usuario: solo IDs permitidos por su membresía activa.
    """
    t = _get_tenant(db, tenant_id)

    if not user.get("is_superadmin"):
        allowed_ids = _ids_from_session_tenants(user.get("tenants", []))
        if tenant_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sin acceso a este tenant")

    request.session["current_tenant_id"] = int(tenant_id)
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

# ------------- Entries (listado mínimo) -------------
@admin_router.get("/entries", response_class=HTMLResponse)
def entries_list(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
    tenant_id: Optional[int] = Query(None),
    section_id: Optional[int] = Query(None),
) -> HTMLResponse:
    tenants_ctx = _tenants_for_context(db, user)

    # Resolver tenant actual
    current_tenant = None
    if tenant_id:
        current_tenant = _get_tenant(db, tenant_id)
        # Verificar acceso si no es superadmin
        if not user.get("is_superadmin"):
            allowed_ids = _ids_from_session_tenants(user.get("tenants", []))
            if tenant_id not in allowed_ids:
                raise HTTPException(status_code=403, detail="Sin acceso a este tenant")
        request.session["current_tenant_id"] = int(tenant_id)
    elif request.session.get("current_tenant_id"):
        current_tenant = _get_tenant(db, int(request.session["current_tenant_id"]))
    elif tenants_ctx:
        # fallback si no hubiera nada en sesión
        current_tenant = _get_tenant(db, tenants_ctx[0]["id"])
        request.session["current_tenant_id"] = int(tenants_ctx[0]["id"])

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

# ------------- Tenants (listado simple) -------------
@admin_router.get("/tenants", response_class=HTMLResponse)
def tenants_list(
    request: Request,
    db: Session = Depends(get_db),
    user = Depends(current_session),
) -> HTMLResponse:
    tenants_ctx = _tenants_for_context(db, user)
    return templates.TemplateResponse(
        "admin/tenants_list.html",
        {"request": request, "user": user, "tenants": tenants_ctx, "tenants_ctx": tenants_ctx},
    )
