# app/security/preview_tokens.py
# ⟶ Utilidades HMAC/JWT para emisión y validación de tokens de preview
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt

from app.core.settings import settings


class PreviewTokenError(Exception):
    pass


def _secret() -> str:
    return settings.PREVIEW_TOKEN_SECRET or settings.JWT_SECRET_KEY


def create_preview_token(
    *,
    tenant_id: int,
    entry_id: int,
    schema_version: int | None = None,
    expires_in: int | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    exp_s = expires_in if expires_in is not None else settings.PREVIEW_TOKEN_EXPIRE_SECONDS
    payload: Dict[str, Any] = {
        "sub": "preview",
        "scope": "preview",
        "tenant_id": tenant_id,
        "entry_id": entry_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_s)).timestamp()),
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    token = jwt.encode(payload, _secret(), algorithm=settings.JWT_ALGORITHM)
    return token


def verify_preview_token(token: str) -> dict:
    try:
        data = jwt.decode(token, _secret(), algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise PreviewTokenError("Preview token expired") from e
    except jwt.InvalidTokenError as e:
        raise PreviewTokenError("Invalid preview token") from e

    if data.get("scope") != "preview" or data.get("sub") != "preview":
        raise PreviewTokenError("Invalid preview token scope")

    # Campos mínimos
    if "tenant_id" not in data or "entry_id" not in data:
        raise PreviewTokenError("Malformed preview token")

    return data
