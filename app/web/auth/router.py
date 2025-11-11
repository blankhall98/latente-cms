# app/web/auth/router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import User, Tenant, UserTenant, Role, UserTenantStatus
from app.services.passwords import verify_password

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")

# Session keys (must match admin)
SESSION_USER_KEY = "user"
SESSION_ACTIVE_TENANT_KEY = "active_tenant"


def _active_status_value() -> str | object:
    """
    Returns the 'active' value for UserTenantStatus regardless of how it's defined.
    Supports enum attributes ACTIVE/Active/active and falls back to 'active'.
    If it's an Enum instance, returns its .value.
    """
    for name in ("ACTIVE", "Active", "active"):
        if hasattr(UserTenantStatus, name):
            val = getattr(UserTenantStatus, name)
            return getattr(val, "value", val)
    # Fallback for string-based status columns
    return "active"


@router.get("/login")
def login_get(request: Request, next: str | None = Query(default=None)):
    # If already logged in, go to /admin (or ?next=)
    session = request.session or {}
    if session.get(SESSION_USER_KEY):
        return RedirectResponse(url=(next or "/admin"), status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request, "next": next or ""})


@router.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    # 1) Normalize & validate credentials
    email_norm = (email or "").strip().lower()
    user = db.scalar(select(User).where(User.email == email_norm))
    if not user or not verify_password(password or "", user.hashed_password or "") or not bool(user.is_active):
        ctx = {
            "request": request,
            "error": "Invalid credentials or inactive user.",
            "next": next or "",
        }
        return templates.TemplateResponse("auth/login.html", ctx, status_code=401)

    # Ensure session dict exists
    if request.session is None:
        # Starlette's SessionMiddleware guarantees a dict, but guard anyway
        request.scope["session"] = {}

    # 2) Store a compact web-session user
    request.session[SESSION_USER_KEY] = {
        "id": int(user.id),
        "email": user.email,
        "is_superadmin": bool(getattr(user, "is_superadmin", False)),
        "full_name": getattr(user, "full_name", "") or "",
    }

    # 3) If user has exactly one active project, set it as active_tenant
    active_val = _active_status_value()
    rows = db.execute(
        select(Tenant, UserTenant, Role)
        .join(UserTenant, UserTenant.tenant_id == Tenant.id)
        .join(Role, Role.id == UserTenant.role_id)
        .where(
            and_(
                UserTenant.user_id == int(user.id),
                UserTenant.status == active_val,
            )
        )
        .order_by(Tenant.name.asc())
    ).all()

    if len(rows) == 1:
        t, _, _ = rows[0]
        request.session[SESSION_ACTIVE_TENANT_KEY] = {
            "id": int(t.id),
            "slug": t.slug,
            "name": t.name,
        }

    # 4) Redirect to next (sanitized) or dashboard
    # Basic safety: only allow relative paths
    target = next or "/admin"
    if not target.startswith("/"):
        target = "/admin"
    return RedirectResponse(url=target, status_code=302)


@router.post("/logout")
def logout_post(request: Request):
    (request.session or {}).clear()
    return RedirectResponse(url="/login", status_code=302)


@router.get("/logout")
def logout_get(request: Request):
    (request.session or {}).clear()
    return RedirectResponse(url="/login", status_code=302)
