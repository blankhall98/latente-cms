# app/deps/auth.py
from __future__ import annotations

from typing import Optional, Callable

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select, exists, and_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import (
    User,
    UserTenant,
    UserTenantStatus,
    RolePermission,
    Permission,
)
from app.security.jwt import decode_token
from jose import ExpiredSignatureError, JWTError


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def _load_user_from_sub(db: Session, sub: str | int) -> Optional[User]:
    try:
        uid = int(sub)
    except Exception:
        return None
    user = db.get(User, uid)
    if not user or not getattr(user, "is_active", True):
        return None
    return user


def get_current_user_id(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> int:
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    sub = payload.get("sub")
    user = _load_user_from_sub(db, sub)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return int(user.id)


def get_current_user_id_optional(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> Optional[int]:
    token = _extract_bearer_token(authorization)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except Exception:
        return None
    sub = payload.get("sub")
    user = _load_user_from_sub(db, sub)
    if not user:
        return None
    return int(user.id)


def get_current_user(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> User:
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    sub = payload.get("sub")
    user = _load_user_from_sub(db, sub)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def user_has_permission(
    db: Session,
    user_id: int,
    tenant_id: int,
    perm_key: str,
) -> bool:
    user = db.get(User, int(user_id))
    if user and getattr(user, "is_superadmin", False):
        return True

    stmt = (
        select(exists().where(
            and_(
                UserTenant.user_id == int(user_id),
                UserTenant.tenant_id == int(tenant_id),
                UserTenant.status == UserTenantStatus.active,
                RolePermission.role_id == UserTenant.role_id,
                Permission.id == RolePermission.permission_id,
                Permission.key == perm_key,
            )
        ))
        .select_from(UserTenant)
        .join(RolePermission, RolePermission.role_id == UserTenant.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
    )
    return bool(db.scalar(stmt) or False)


def require_permission(perm_key: str) -> Callable:
    def _dep(
        tenant_id: int = Query(..., description="Tenant ID (query param)"),
        db: Session = Depends(get_db),
        user_id: int = Depends(get_current_user_id),
    ) -> None:
        if not user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key=perm_key):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {perm_key}")
        return None
    return _dep




