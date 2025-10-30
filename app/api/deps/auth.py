# app/api/deps/auth.py
# ── Dependencias de autenticación/autorización para FastAPI (RBAC real por permisos)
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import (
    UserTenant,
    UserTenantStatus,
    RolePermission,
    Permission,
)

# ---------------------------
# Identidad (temporal por header)
# ---------------------------
def get_current_user_id(x_user_id: int | None = Header(default=None, alias="X-User-Id")) -> int:
    """
    En producción sustituir por validación JWT real.
    Para pruebas, tomamos el user_id del header 'X-User-Id'.
    """
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="Missing X-User-Id (replace with real JWT auth)")
    return x_user_id


def get_current_user_id_optional(x_user_id: int | None = Header(default=None, alias="X-User-Id")) -> int | None:
    """
    Variante opcional: no lanza 401 si falta el header.
    Útil para endpoints que permiten token de preview o auth opcional.
    """
    return x_user_id


# ---------------------------
# Autorización por permisos
# ---------------------------
def user_has_permission(db: Session, user_id: int, tenant_id: int, perm_key: str) -> bool:
    """
    Verifica si el usuario (user_id) dentro del tenant (tenant_id)
    tiene asignado (vía su rol) el permiso cuyo key == perm_key.
    Política 100% basada en permisos (sin lista blanca de roles).

    Retorna True/False.
    """
    # Debe existir un vínculo UserTenant ACTIVO en ese tenant
    ut_subq = (
        select(UserTenant.id)
        .where(
            and_(
                UserTenant.user_id == user_id,
                UserTenant.tenant_id == tenant_id,
                UserTenant.status == UserTenantStatus.active,
            )
        )
        .limit(1)
        .scalar_subquery()
    )

    # Existe un RolePermission que vincula el rol del UserTenant con un Permission(key=perm_key)
    exists_stmt = (
        select(1)
        .select_from(RolePermission)
        .join(Permission, RolePermission.permission_id == Permission.id)
        .where(
            and_(
                # role_id del UserTenant activo
                RolePermission.role_id == select(UserTenant.role_id).where(UserTenant.id == ut_subq).scalar_subquery(),
                Permission.key == perm_key,
            )
        )
        .limit(1)
    )

    return db.scalar(exists_stmt) is not None


def require_permission(perm_key: str):
    """
    Crea una dependencia que exige `perm_key` para el tenant indicado.
    Requiere que el endpoint reciba `tenant_id` como query param.
    """
    def _dep(
        tenant_id: int | None = None,  # FastAPI lo inyecta desde query si existe
        user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_db),
    ):
        if tenant_id is None:
            raise HTTPException(status_code=400, detail="tenant_id is required for permission check")

        if not user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key=perm_key):
            raise HTTPException(status_code=403, detail=f"Missing permission: {perm_key}")
        return True

    return _dep




