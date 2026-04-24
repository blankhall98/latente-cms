from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import get_db
from app.models.auth import Tenant
from app.models.content import Entry, Section
from app.services.mail_service import send_contact_email

router = APIRouter(prefix="/delivery/v1", tags=["Contact"])

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

class ContactFormIn(BaseModel):
    tenant_slug: str = Field(..., min_length=1, max_length=80)
    sender_email: EmailStr
    # Free-form fields — the front-end decides what the form contains.
    # e.g. {"Name": "Carlos", "Message": "Hello", "Budget": "$50k"}
    fields: dict[str, str] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/contact", summary="Submit a contact form (public)")
def submit_contact_form(
    payload: ContactFormIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Public endpoint consumed by front-end contact forms.

    Only tenant_slug and sender_email are required. All form fields
    (name, message, subject, budget, etc.) are passed as a free-form
    dict and rendered verbatim in the email.

    The receiver address is read from the tenant's published 'settings'
    entry — never from the request body.
    """
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    # Resolve tenant
    tenant = db.scalar(
        select(Tenant).where(
            Tenant.slug == payload.tenant_slug,
            Tenant.is_active.is_(True),
        )
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    # Fetch contact_email from the tenant's published settings entry
    settings_entry = db.scalar(
        select(Entry)
        .join(Section, Section.id == Entry.section_id)
        .where(
            Entry.tenant_id == tenant.id,
            Section.key == "settings",
            Entry.status == "published",
        )
        .order_by(Entry.updated_at.desc())
        .limit(1)
    )

    contact_email: str | None = None
    if settings_entry and isinstance(settings_entry.data, dict):
        contact_email = settings_entry.data.get("contact_email") or None

    if not contact_email:
        raise HTTPException(
            status_code=503,
            detail="Contact form is not configured for this site.",
        )

    # Derive subject and sender name from fields if present, with fallbacks.
    sender_name = payload.fields.get("Name") or payload.fields.get("name") or "Website visitor"
    subject = (
        payload.fields.get("Subject")
        or payload.fields.get("subject")
        or f"New contact form message — {tenant.name}"
    )

    try:
        send_contact_email(
            to_email=contact_email,
            sender_name=sender_name,
            sender_email=str(payload.sender_email),
            subject=subject,
            fields=payload.fields,
            tenant_name=tenant.name,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Mail service is not configured.") from exc
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Failed to send message. Please try again later.",
        )

    return {"ok": True}
