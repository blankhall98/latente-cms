# app/api/deps/auth.py
# ── Dependencias de autenticación/autorización para FastAPI (RBAC por permisos)
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import and_, select, func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import (
    UserTenant,
    User,
    UserTenantStatus,
    Role,
    RolePermission,
    Permission,
)

# ---------------------------
# Identidad (temporal por header)
# ---------------------------
def get_current_user_id(
    x_user_id: int | None = Header(default=None, alias="X-User-Id")
) -> int:
    """
    En producción sustituir por validación JWT real.
    Para pruebas, tomamos el user_id del header 'X-User-Id'.
    """
    if x_user_id is None:
        raise HTTPException(
            status_code=401,
            detail="Missing X-User-Id (replace with real JWT auth)",
        )
    return x_user_id


def get_current_user_id_optional(
    x_user_id: int | None = Header(default=None, alias="X-User-Id")
) -> int | None:
    """
    Variante opcional: no lanza 401 si falta el header.
    Útil para endpoints que permiten token de preview o auth opcional.
    """
    return x_user_id


# ---------------------------
# Autorización por permisos (robusta con INNER JOINs)
# ---------------------------
def user_has_permission(db, user_id: int | None, tenant_id: int | None, perm_key: str) -> bool:
    # --- BYPASS SUPERADMIN (nuevo) ---
    if user_id:
        u = db.get(User, user_id)
        if u and getattr(u, "is_superadmin", False):
            return True
    # ---------------------------------

    if not user_id or not tenant_id:
        return False

    # (resto de tu consulta por joins)
    stmt = (
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserTenant, UserTenant.role_id == Role.id)
        .where(
            UserTenant.user_id == user_id,
            UserTenant.tenant_id == tenant_id,
            Permission.key == perm_key
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None


def _resolve_tenant_id(possible_tenant_id: int | None, request: Request) -> int:
    """
    Resuelve tenant_id desde:
      1) Argumento inyectado por FastAPI (si el endpoint lo recibe)
      2) Query string (?tenant_id=)
      3) Path params (/.../{tenant_id}/...)
    """
    if possible_tenant_id is not None:
        return int(possible_tenant_id)

    # query param
    qv = request.query_params.get("tenant_id")
    if qv is not None:
        try:
            return int(qv)
        except ValueError:
            raise HTTPException(status_code=400, detail="tenant_id must be an integer")

    # path param
    pv = request.path_params.get("tenant_id")
    if pv is not None:
        try:
            return int(pv)
        except ValueError:
            raise HTTPException(status_code=400, detail="tenant_id must be an integer")

    raise HTTPException(status_code=400, detail="tenant_id is required for permission check")


def require_permission(perm_key: str):
    """
    Crea una dependencia que exige `perm_key` para el tenant indicado.
    No fija el origen de tenant_id (query/path); se resuelve dinámicamente.
    """
    def _dep(
        request: Request,
        tenant_id: int | None = None,     # puede venir del endpoint si lo declara
        user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_db),
    ):
        resolved_tenant_id = _resolve_tenant_id(tenant_id, request)
        if not user_has_permission(db, user_id=user_id, tenant_id=resolved_tenant_id, perm_key=perm_key):
            raise HTTPException(status_code=403, detail=f"Missing permission: {perm_key}")
        return True

    return _dep







