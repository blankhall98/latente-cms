# app/security/jwt.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import jwt, JWTError
from app.core.settings import settings

ALGO   = settings.JWT_ALGORITHM or "HS256"
SECRET = settings.JWT_SECRET_KEY or "dev-secret"
ACCESS_MIN  = settings.ACCESS_MIN
REFRESH_MIN = settings.REFRESH_MIN

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _exp_ts(minutes: int) -> int:
    # exp como entero UNIX (segundos), más compatible
    return int((_utcnow() + timedelta(minutes=minutes)).timestamp())

def _iat_ts() -> int:
    return int(_utcnow().timestamp())

def create_access_token(subject: int | str, extra: Dict[str, Any] | None = None) -> str:
    payload: Dict[str, Any] = {
        "sub": str(subject),
        "type": "access",
        "iat": _iat_ts(),
        "exp": _exp_ts(ACCESS_MIN),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, SECRET, algorithm=ALGO)

def create_refresh_token(subject: int | str, extra: Dict[str, Any] | None = None) -> str:
    payload: Dict[str, Any] = {
        "sub": str(subject),
        "type": "refresh",
        "iat": _iat_ts(),
        "exp": _exp_ts(REFRESH_MIN),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, SECRET, algorithm=ALGO)

def decode_token(token: str) -> Dict[str, Any]:
    try:
        # Sin 'leeway' (python-jose no lo soporta)
        # quitamos aud/iss porque no los firmamos
        return jwt.decode(
            token,
            SECRET,
            algorithms=[ALGO],
            options={"verify_aud": False, "verify_iss": False},
        )
    except JWTError as e:
        # re-lanzamos para que el caller haga 401
        raise
