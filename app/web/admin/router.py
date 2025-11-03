# app/web/admin/router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant, UserTenant, UserTenantStatus, Role

# IMPORTANT: prefix="/admin" so /admin, /admin/projects, etc.
router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")

SESSION_USER_KEY = "user"
SESSION_ACTIVE_TENANT_KEY = "active_tenant"


def _require_login(request: Request) -> dict:
    auth = (request.session or {}).get(SESSION_USER_KEY)
    if not auth:
        raise PermissionError
    return auth


def _active_status_value() -> str | object:
    # Defensive resolver for Enum/str differences
    for name in ("ACTIVE", "Active", "active"):
        if hasattr(UserTenantStatus, name):
            val = getattr(UserTenantStatus, name)
            return getattr(val, "value", val)
    return "active"


@router.get("")
def admin_dashboard(request: Request):
    try:
        auth = _require_login(request)
    except PermissionError:
        return RedirectResponse(url="/login", status_code=302)

    # Mock data (wired to live data in Step 8B)
    kpis = [
        {"label": "Pages", "value": "12", "suffix": "published"},
        {"label": "Sections", "value": "38", "suffix": "in project"},
        {"label": "Projects", "value": "2", "suffix": "active"},
    ]
    recent_entries = [
        {"title": "Home", "sub": "Landing / OWA · published"},
        {"title": "Therapies", "sub": "Landing / OWA · draft"},
        {"title": "Plans", "sub": "Landing / OWA · published"},
    ]
    quick_links = [
        {"href": "/admin/entries", "title": "Pages", "sub": "Manage content"},
        {"href": "/admin/schemas", "title": "Sections", "sub": "Model structures"},
        {"href": "/admin/members", "title": "Members", "sub": "Roles & access"},
        {"href": "/admin/projects", "title": "Projects", "sub": "Switch active project"},
    ]

    current_tenant = (request.session or {}).get(SESSION_ACTIVE_TENANT_KEY) or {
        "name": "—", "slug": None, "id": None
    }

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": {"email": ((request.session or {}).get(SESSION_USER_KEY) or {}).get("email")},
            "kpis": kpis,
            "recent_entries": recent_entries,
            "quick_links": quick_links,
            "current_tenant": current_tenant,
        },
    )


def _require_web_user(request: Request) -> dict:
    user = (request.session or {}).get(SESSION_USER_KEY)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _set_active_tenant(request: Request, tenant_id: int, tenant_slug: str, tenant_name: str) -> None:
    request.session[SESSION_ACTIVE_TENANT_KEY] = {
        "id": tenant_id,
        "slug": tenant_slug,
        "name": tenant_name,
    }


def _get_active_tenant(request: Request) -> dict | None:
    return (request.session or {}).get(SESSION_ACTIVE_TENANT_KEY)


@router.get("/projects")
def projects_list(request: Request, db: Session = Depends(get_db)):
    user = _require_web_user(request)
    active_val = _active_status_value()

    # Superadmin: list ALL tenants (no membership required)
    if user.get("is_superadmin"):
        rows = db.execute(
            select(Tenant)
            .order_by(Tenant.name.asc())
        ).all()

        items = []
        for (t,) in rows:
            items.append({
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "role": "superadmin",
                "role_label": "Superadmin",
                "status": "active",
            })
    else:
        # Regular user: list only tenants with active membership
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


@router.post("/projects/{tenant_id}/set-active")
def set_active_project(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_web_user(request)
    active_val = _active_status_value()

    # Superadmin may activate ANY tenant
    if user.get("is_superadmin"):
        t = db.get(Tenant, tenant_id)
        if not t:
            raise HTTPException(status_code=404, detail="Project not found.")
        _set_active_tenant(request, t.id, t.slug, t.name)
        return RedirectResponse(url="/admin", status_code=303)

    # Regular user: must belong to the project
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





