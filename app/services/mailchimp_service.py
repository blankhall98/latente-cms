from __future__ import annotations

import hashlib
import os
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


def _credentials_for_tenant(tenant_slug: str) -> tuple[str, str, str]:
    """
    Resolve Mailchimp credentials for *tenant_slug* from env vars.

    Looks for:
      MAILCHIMP_API_KEY_<SLUG>       e.g. MAILCHIMP_API_KEY_ANRO
      MAILCHIMP_AUDIENCE_ID_<SLUG>   e.g. MAILCHIMP_AUDIENCE_ID_ANRO

    The server prefix is inferred from the API key suffix (e.g. "us1").

    Returns (api_key, audience_id, server_prefix).
    Raises MailchimpConfigurationError if any value is missing.
    """
    slug = tenant_slug.upper()
    api_key = os.environ.get(f"MAILCHIMP_API_KEY_{slug}", "").strip()
    audience_id = os.environ.get(f"MAILCHIMP_AUDIENCE_ID_{slug}", "").strip()
    server_prefix = _infer_server_prefix(api_key)

    if not api_key:
        raise MailchimpConfigurationError(
            f"MAILCHIMP_API_KEY_{slug} is not configured"
        )
    if not audience_id:
        raise MailchimpConfigurationError(
            f"MAILCHIMP_AUDIENCE_ID_{slug} is not configured"
        )
    if not server_prefix:
        raise MailchimpConfigurationError(
            f"Could not infer Mailchimp server prefix from MAILCHIMP_API_KEY_{slug}"
        )
    return api_key, audience_id, server_prefix


def subscribe_email(
    email: str,
    *,
    api_key: str | None = None,
    audience_id: str | None = None,
    server_prefix: str | None = None,
) -> dict[str, Any]:
    """
    Subscribe *email* to a Mailchimp audience.

    Credentials can be passed explicitly (used by the generic endpoint)
    or omitted to fall back to the legacy global settings vars
    (used by the existing OWA endpoint — backwards compatible).
    """
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        raise MailchimpRequestError("Email is required")

    # Fall back to global settings when no explicit credentials provided
    # (keeps the existing OWA endpoint working without any changes).
    _api_key = (api_key or settings.MAILCHIMP_API_KEY or "").strip()
    _audience_id = (audience_id or settings.MAILCHIMP_AUDIENCE_ID or "").strip()
    _server_prefix = (
        server_prefix
        or (settings.MAILCHIMP_SERVER_PREFIX or "").strip()
        or _infer_server_prefix(_api_key)
    )

    if not _api_key:
        raise MailchimpConfigurationError("MAILCHIMP_API_KEY is not configured")
    if not _audience_id:
        raise MailchimpConfigurationError("MAILCHIMP_AUDIENCE_ID is not configured")
    if not _server_prefix:
        raise MailchimpConfigurationError(
            "MAILCHIMP_SERVER_PREFIX is not configured and could not be inferred"
        )

    base_url = f"https://{_server_prefix}.api.mailchimp.com/3.0"
    subscriber_hash = hashlib.md5(normalized_email.encode("utf-8")).hexdigest()
    url = f"{base_url}/lists/{_audience_id}/members/{subscriber_hash}"

    payload = {
        "email_address": normalized_email,
        "status_if_new": "subscribed",
        "status": "subscribed",
    }

    timeout = float(getattr(settings, "MAILCHIMP_TIMEOUT_SECONDS", 10) or 10)
    try:
        response = httpx.put(
            url,
            auth=("anystring", _api_key),
            json=payload,
            params={"skip_merge_validation": "true"},
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        raise MailchimpRequestError(f"Mailchimp request failed: {exc}") from exc

    if response.status_code not in (200, 201):
        detail = None
        try:
            body = response.json()
            detail = body.get("detail") or body.get("title")
            if not detail and isinstance(body.get("errors"), list) and body["errors"]:
                first_err = body["errors"][0]
                detail = first_err.get("message") or first_err.get("field")
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
