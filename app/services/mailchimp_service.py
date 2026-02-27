from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.core.settings import settings


class MailchimpConfigurationError(Exception):
    pass


class MailchimpRequestError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _infer_server_prefix(api_key: str | None) -> str | None:
    if not api_key:
        return None
    key = api_key.strip()
    if "-" not in key:
        return None
    suffix = key.rsplit("-", 1)[-1].strip().lower()
    return suffix or None


def _get_mailchimp_base_url() -> str:
    api_key = (settings.MAILCHIMP_API_KEY or "").strip()
    audience_id = (settings.MAILCHIMP_AUDIENCE_ID or "").strip()
    server_prefix = (settings.MAILCHIMP_SERVER_PREFIX or "").strip() or _infer_server_prefix(api_key)

    if not api_key:
        raise MailchimpConfigurationError("MAILCHIMP_API_KEY is not configured")
    if not audience_id:
        raise MailchimpConfigurationError("MAILCHIMP_AUDIENCE_ID is not configured")
    if not server_prefix:
        raise MailchimpConfigurationError(
            "MAILCHIMP_SERVER_PREFIX is not configured and could not be inferred from MAILCHIMP_API_KEY"
        )
    return f"https://{server_prefix}.api.mailchimp.com/3.0"


def subscribe_email(email: str) -> dict[str, Any]:
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        raise MailchimpRequestError("Email is required")

    api_key = (settings.MAILCHIMP_API_KEY or "").strip()
    audience_id = (settings.MAILCHIMP_AUDIENCE_ID or "").strip()
    base_url = _get_mailchimp_base_url()
    subscriber_hash = hashlib.md5(normalized_email.encode("utf-8")).hexdigest()

    url = f"{base_url}/lists/{audience_id}/members/{subscriber_hash}"
    payload = {
        "email_address": normalized_email,
        "status_if_new": "subscribed",
        "status": "subscribed",
    }

    timeout = float(getattr(settings, "MAILCHIMP_TIMEOUT_SECONDS", 10) or 10)
    try:
        response = httpx.put(
            url,
            auth=("anystring", api_key),
            json=payload,
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        raise MailchimpRequestError(f"Mailchimp request failed: {exc}") from exc

    if response.status_code not in (200, 201):
        detail = None
        try:
            body = response.json()
            detail = body.get("detail") or body.get("title")
        except Exception:
            detail = response.text[:200]
        raise MailchimpRequestError(
            f"Mailchimp rejected subscription ({response.status_code}): {detail or 'unknown error'}",
            status_code=response.status_code,
        )

    data: dict[str, Any] = {}
    try:
        data = response.json()
    except Exception:
        data = {}

    return {
        "email": normalized_email,
        "subscriber_hash": subscriber_hash,
        "mailchimp_id": data.get("id"),
        "status": data.get("status", "subscribed"),
    }
