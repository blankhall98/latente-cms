from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.core.settings import settings
from app.services.mailchimp_service import (
    MailchimpConfigurationError,
    MailchimpRequestError,
    _credentials_for_tenant,
    subscribe_email,
)

router = APIRouter(prefix="/delivery/v1", tags=["Newsletter"])

# ---------------------------------------------------------------------------
# In-memory IP rate limiter
# ---------------------------------------------------------------------------
_rate_store: dict[str, list[float]] = defaultdict(list)
_WINDOW_SECONDS = 60.0


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    recent = [t for t in _rate_store[ip] if t > cutoff]
    if len(recent) >= settings.CONTACT_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment before trying again.",
        )
    recent.append(now)
    _rate_store[ip] = recent


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class NewsletterSubscribeIn(BaseModel):
    tenant_slug: str = Field(..., min_length=1, max_length=80)
    email: EmailStr


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/newsletter-subscribe", summary="Subscribe to a tenant newsletter (public)")
def newsletter_subscribe(
    payload: NewsletterSubscribeIn,
    request: Request,
):
    """
    Generic newsletter subscription endpoint.

    Credentials are resolved from env vars keyed by tenant slug:
      MAILCHIMP_API_KEY_<SLUG>
      MAILCHIMP_AUDIENCE_ID_<SLUG>

    No database access needed — fully stateless.
    """
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    try:
        api_key, audience_id, server_prefix = _credentials_for_tenant(payload.tenant_slug)
    except MailchimpConfigurationError:
        raise HTTPException(
            status_code=503,
            detail="Newsletter subscription is not configured for this site.",
        )

    try:
        result = subscribe_email(
            str(payload.email),
            api_key=api_key,
            audience_id=audience_id,
            server_prefix=server_prefix,
        )
    except MailchimpConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MailchimpRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, **result}
