# app/deps/auth.py
from __future__ import annotations

from typing import Optional, Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import User
from app.security.jwt import decode_token
from app.services.authz import user_has_permission as _svc_user_has_permission

# Reusable HTTP bearer scheme (non-fatal if header is missing)
_bearer = HTTPBearer(auto_error=False)


# -----------------------------
# Helpers
# -----------------------------
def _load_user_from_sub(db: Session, sub: str | int) -> Optional[User]:
    try:
        uid = int(sub)
    except Exception:
        return None
    user = db.get(User, uid)
    if not user or not getattr(user, "is_active", True):
        return None
    return user


def _decode_and_get_user_id(db: Session, token: str) -> int:
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


# -----------------------------
# Public dependencies
# -----------------------------
def get_current_user_id(
    db: Session = Depends(get_db),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> int:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return _decode_and_get_user_id(db, creds.credentials)


def get_current_user_id_optional(
    db: Session = Depends(get_db),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[int]:
    if not creds or not creds.credentials:
        return None
    try:
        return _decode_and_get_user_id(db, creds.credentials)
    except HTTPException:
        # Treat bad token as unauthenticated when optional
        return None


def get_current_user(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> User:
    user = db.get(User, user_id)
    if not user or not getattr(user, "is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


# -----------------------------
# Permission helpers (exports preserved)
# -----------------------------
def user_has_permission(
    db: Session,
    user_id: int,
    tenant_id: int,
    perm_key: str,
) -> bool:
    """
    Keep this symbol here for backward compatibility,
    but delegate to the service-layer single source of truth.
    """
    # Superadmin bypass handled inside endpoints via get_current_user when needed.
    return _svc_user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key=perm_key)


def require_permission(perm_key: str) -> Callable:
    """
    Usage:
        @router.get(..., dependencies=[Depends(require_permission("content:read"))])
    Notes:
      • Superadmins bypass checks.
      • tenant_id can come from ?tenant_id=... (query) OR from the path params.
    """
    def _dep(
        request: Request,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> None:
        # Superadmin bypass
        if getattr(current_user, "is_superadmin", False):
            return

        # Resolve tenant_id from query first, then path params
        tenant_id: Optional[int] = None
        q_val = request.query_params.get("tenant_id")
        if q_val is not None:
            try:
                tenant_id = int(q_val)
            except ValueError:
                raise HTTPException(status_code=422, detail="tenant_id must be an integer")

        if tenant_id is None:
            p_val = request.path_params.get("tenant_id")
            if p_val is not None:
                try:
                    tenant_id = int(p_val)
                except ValueError:
                    raise HTTPException(status_code=422, detail="tenant_id must be an integer")

        if tenant_id is None:
            raise HTTPException(status_code=422, detail="tenant_id is required (query or path)")

        if not user_has_permission(db, user_id=current_user.id, tenant_id=tenant_id, perm_key=perm_key):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {perm_key}")

    return _dep




