# app/services/authz.py
# ── Verificación de permisos por usuario/tenant/permiso
from __future__ import annotations
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models.auth import UserTenant, RolePermission, Permission, Role

def user_has_permission(db: Session, *, user_id: int, tenant_id: int, perm_key: str) -> bool:
    """
    Retorna True si el usuario tiene el permiso `perm_key` en el tenant dado.
    Se evalúa vía UserTenant -> Role -> RolePermission -> Permission.
    """
    # Join lógico:
    # UserTenant (user_id, tenant_id, role_id)
    # RolePermission (role_id, permission_id)
    # Permission (key == perm_key)
    stmt = (
        select(Permission.id)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserTenant, UserTenant.role_id == Role.id)
        .where(
            and_(
                UserTenant.user_id == user_id,
                UserTenant.tenant_id == tenant_id,
                Permission.key == perm_key,
            )
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None
