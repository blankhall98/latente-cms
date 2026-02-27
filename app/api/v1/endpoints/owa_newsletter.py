from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.services.mailchimp_service import (
    MailchimpConfigurationError,
    MailchimpRequestError,
    subscribe_email,
)


router = APIRouter()


class OwaNewsletterIn(BaseModel):
    email: EmailStr = Field(..., max_length=320)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OwaNewsletterIn":
        mapped = dict(payload or {})
        if "email" not in mapped:
            mapped["email"] = mapped.get("email_address") or mapped.get("emailAddress")
        return cls.model_validate(mapped)


@router.post("/owa/newsletter-subscriptions")
def subscribe_owa_newsletter(payload: dict[str, Any]):
    try:
        data = OwaNewsletterIn.from_payload(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}") from exc

    try:
        result = subscribe_email(str(data.email))
    except MailchimpConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MailchimpRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "ok": True,
        "endpoint": "POST /api/v1/owa/newsletter-subscriptions",
        **result,
    }
