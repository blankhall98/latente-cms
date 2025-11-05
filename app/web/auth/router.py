# app/web/auth/router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
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
def login_get(request: Request):
    # If already logged in, go to /admin
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
    # 1) Validate user/password against DB (same as API)
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.hashed_password or "") or not user.is_active:
        ctx = {"request": request, "error": "Invalid credentials or inactive user."}
        return templates.TemplateResponse("auth/login.html", ctx, status_code=401)

    # 2) Store a compact web-session user
    request.session[SESSION_USER_KEY] = {
        "id": int(user.id),
        "email": user.email,
        "is_superadmin": bool(user.is_superadmin),
        "full_name": user.full_name or "",
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

    # 4) Go to dashboard
    return RedirectResponse(url="/admin", status_code=302)


@router.post("/logout")
def logout_post(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@router.get("/logout")
def logout_get(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
