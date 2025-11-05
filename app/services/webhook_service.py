# app/services/webhook_service.py
from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
import time
from typing import Any, Dict, List, Tuple

import httpx

from app.core.settings import settings

# Estructura mínima esperada de endpoints
# [{"url": "...", "secret": "...", "events": ["content.published", ...]}]

def get_endpoints_for_tenant(db, tenant_id: int) -> List[Dict[str, Any]]:
    """
    MVP: devolvemos lista desde DB si existe el modelo WebhookEndpoint.
    Si el modelo/tabla no existe (no migrado aún), devolvemos lista vacía.
    Los tests pueden monkeypatchear este método para devolver endpoints.
    """
    try:
        from app.models.webhook import WebhookEndpoint  # type: ignore
    except Exception:
        return []

    try:
        eps = (
            db.query(WebhookEndpoint)
            .filter(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.is_enabled.is_(True))
            .all()
        )
        out: List[Dict[str, Any]] = []
        for ep in eps:
            out.append(
                {
                    "url": ep.url,
                    "secret": ep.secret,
                    "events": ep.event_filter or ["content.published", "content.unpublished", "content.archived"],
                }
            )
        return out
    except Exception:
        # Si la tabla no existe o falla la consulta, evitamos romper publish.
        return []


def _sign(secret: str, timestamp: str, body_bytes: bytes) -> str:
    # Firma: SHA256-HMAC sobre "<ts>." + body
    msg = (timestamp + ".").encode("utf-8") + body_bytes
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


async def _deliver_once(url: str, headers: Dict[str, str], body: bytes, timeout: int) -> Tuple[bool, int | None]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, content=body, headers=headers)
        return (200 <= resp.status_code < 300, resp.status_code)


async def _deliver_with_retries(
    url: str,
    headers: Dict[str, str],
    body: bytes,
    timeout: int,
    max_retries: int,
    backoff_seconds: int,
) -> Tuple[bool, int | None]:
    for attempt in range(1, max_retries + 1):
        ok, code = await _deliver_once(url, headers, body, timeout)
        if ok:
            return True, code
        await asyncio.sleep(backoff_seconds * attempt)
    return False, None


async def emit_event_async(db, tenant_id: int, event: str, payload: Dict[str, Any]) -> None:
    if not settings.WEBHOOKS_ENABLED:
        return

    endpoints = get_endpoints_for_tenant(db, tenant_id)
    if not endpoints:
        return

    # Cuerpo (estable, sin espacios)
    body_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))

    tasks = []
    for ep in endpoints:
        events = ep.get("events") or []
        if events and (event not in events):
            continue

        secret = str(ep["secret"])
        sig = _sign(secret, ts, body_bytes)

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event,
            "X-Webhook-Timestamp": ts,
            "X-Webhook-Signature": sig,
        }

        tasks.append(
            _deliver_with_retries(
                url=str(ep["url"]),
                headers=headers,
                body=body_bytes,
                timeout=int(settings.WEBHOOKS_TIMEOUT_SECONDS),
                max_retries=int(settings.WEBHOOKS_MAX_RETRIES),
                backoff_seconds=int(settings.WEBHOOKS_BACKOFF_SECONDS),
            )
        )

    if not tasks:
        return

    if settings.WEBHOOKS_SYNC_FOR_TEST:
        # Ejecutar en serie para que el test pueda afirmar resultados sin carreras
        for t in tasks:
            await t
    else:
        # Despacho concurrente sin bloquear la respuesta del endpoint
        for coro in tasks:
            asyncio.create_task(coro)
