# app/web/auth/router.py
from __future__ import annotations
from datetime import timedelta

from fastapi import APIRouter, Request, Form, status, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import get_db
from app.models.auth import User, Tenant, UserTenant, UserTenantStatus
from app.services.passwords import verify_password

auth_router = APIRouter(tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

SESSION_KEY = "user"  # {"user_id":..., "email":..., "is_superadmin":..., "tenants":[{"id":..,"slug":"..","name":".."}]}

# --- Helpers de sesión ---
def _login_user(request: Request, *, user_id: int, email: str, is_superadmin: bool, tenants: list[dict]):
    request.session[SESSION_KEY] = {
        "user_id": user_id,
        "email": email,
        "is_superadmin": is_superadmin,
        "tenants": tenants,  # lista de dicts {id, slug, name}
    }

def _logout_user(request: Request):
    request.session.pop(SESSION_KEY, None)
    request.session.pop("current_tenant_id", None)

def _get_user(request: Request):
    return request.session.get(SESSION_KEY)

# --- Vistas ---
@auth_router.get("/login")
def login_form(request: Request):
    if _get_user(request):
        return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("auth/login.html", {"request": request})

@auth_router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    email_norm = email.strip().lower()

    # 1) Usuario real
    user = db.scalar(select(User).where(User.email == email_norm))
    if not user or not user.hashed_password or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Correo o contraseña inválidos."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not user.is_active:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Cuenta inactiva."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 2) Membresías activas
    tenant_rows = db.execute(
        select(Tenant.id, Tenant.slug, Tenant.name)
        .join(UserTenant, UserTenant.tenant_id == Tenant.id)
        .where(UserTenant.user_id == user.id, UserTenant.status == UserTenantStatus.active)
        .order_by(Tenant.id.asc())
    ).all()
    tenant_list = [{"id": r.id, "slug": r.slug, "name": r.name} for r in tenant_rows]

    # 3) Guardar sesión (incluimos tenants también para superadmin; nos sirve para UI)
    _login_user(
        request,
        user_id=user.id,
        email=user.email,
        is_superadmin=bool(user.is_superadmin),
        tenants=tenant_list,
    )

    # 4) Limpiamos tenant actual previo (si lo hubiera)
    request.session.pop("current_tenant_id", None)

    # 5) Redirigir
    resp = RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        key="session",
        value=request.cookies.get("session", ""),
        max_age=int(timedelta(hours=8).total_seconds()),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="lax",
    )
    return resp

@auth_router.get("/logout")
def logout(request: Request):
    _logout_user(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)




