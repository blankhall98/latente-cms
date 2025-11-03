# app/api/v1/auth.py
from __future__ import annotations
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Response, Header, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import User, UserTenant, Tenant, Role
from app.security.jwt import create_access_token, create_refresh_token, decode_token
from app.services.passwords import verify_password
from app.deps.auth import get_current_user  # dependencia estándar para /me

router = APIRouter(tags=["auth"])  # el prefix lo pone api/v1/router.py


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str

class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshIn(BaseModel):
    refresh_token: str

class MeOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_superadmin: bool
    memberships: list[dict]


# ---------------------------------------------------------------------
# Helpers locales
# ---------------------------------------------------------------------
def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Devuelve solo el token si viene como 'Authorization: Bearer <jwt>'."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------
@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.hashed_password or ""):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive")

    extra = {"email": user.email, "is_superadmin": user.is_superadmin}
    return TokenOut(
        access_token=create_access_token(user.id, extra),
        refresh_token=create_refresh_token(user.id, extra),
    )


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn):
    try:
        payload = decode_token(body.refresh_token)
        sub = payload.get("sub")
        extra = {k: v for k, v in payload.items() if k not in {"sub", "iat", "exp"}}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    return TokenOut(
        access_token=create_access_token(sub, extra),
        refresh_token=create_refresh_token(sub, extra),
    )


@router.get("/me", response_model=MeOut)
def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (
        select(UserTenant, Tenant, Role)
        .join(Tenant, UserTenant.tenant_id == Tenant.id)
        .join(Role, UserTenant.role_id == Role.id)
        .where(UserTenant.user_id == current_user.id)
    )
    memberships = []
    for ut, t, r in db.execute(q).all():
        memberships.append({
            "tenant_id": t.id,
            "tenant_slug": t.slug,
            "tenant_name": t.name,
            "role": r.key,
            "status": ut.status.value if hasattr(ut.status, "value") else str(ut.status),
        })

    return MeOut(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_superadmin=current_user.is_superadmin,
        memberships=memberships,
    )


@router.post("/logout", status_code=204)
def logout(_: Response):
    # JWT stateless: client-side logout
    return Response(status_code=204)


# ---------------------------------------------------------------------
# /introspect — diagnóstico simple del token
# Lee el JWT desde el HEADER Authorization: Bearer <token>
# y, opcionalmente, desde el query ?authorization=<token>
# ---------------------------------------------------------------------
@router.get("/introspect")
def introspect(
    authorization_hdr: Optional[str] = Header(default=None, alias="Authorization"),
    authorization_qs: Optional[str] = Query(default=None, alias="authorization"),
):
    # Prioriza header Bearer; si no viene, usa ?authorization=
    def _extract_bearer_token(h: Optional[str]) -> Optional[str]:
        if not h:
            return None
        parts = h.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        return None

    token = _extract_bearer_token(authorization_hdr) or authorization_qs
    if not token:
        return {"ok": False, "detail": "Missing Authorization Bearer token", "claims": None}

    try:
        claims = decode_token(token)
        # extras legibles
        from datetime import datetime, timezone
        claims_pretty = dict(claims)
        if isinstance(claims.get("iat"), int):
            claims_pretty["_iat_iso"] = datetime.fromtimestamp(claims["iat"], tz=timezone.utc).isoformat()
        if isinstance(claims.get("exp"), int):
            claims_pretty["_exp_iso"] = datetime.fromtimestamp(claims["exp"], tz=timezone.utc).isoformat()
        return {"ok": True, "detail": "valid", "claims": claims_pretty}
    except Exception as e:
        # ← ahora verás exactamente por qué falla
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "claims": None}



