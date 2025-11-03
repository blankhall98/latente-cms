# app/deps/auth.py
from __future__ import annotations
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select
from jose import JWTError

from app.db.session import get_db
from app.models.auth import User
from app.security.jwt import decode_token

bearer_scheme = HTTPBearer(auto_error=False)

def _auth_error(detail="Not authenticated"):
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

def get_current_user_id(
    req: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> int:
    """Obtiene user_id desde Authorization: Bearer <JWT>. (Stateless)"""
    if not creds or not creds.scheme.lower() == "bearer":
        _auth_error()
    token = creds.credentials
    try:
        payload = decode_token(token)
    except JWTError:
        _auth_error("Invalid or expired token")
    sub = payload.get("sub")
    if sub is None:
        _auth_error("Malformed token (no sub)")
    try:
        return int(sub)
    except Exception:
        _auth_error("Malformed sub")

def get_current_user(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)) -> User:
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        _auth_error("User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive")
    return user

def get_current_user_optional(
    db: Session = Depends(get_db),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User | None:
    """Versión opcional: si no hay token, retorna None."""
    if not creds:
        return None
    try:
        payload = decode_token(creds.credentials)
        user_id = int(payload.get("sub"))
    except Exception:
        return None
    return db.get(User, user_id)







