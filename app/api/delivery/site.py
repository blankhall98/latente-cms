from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.delivery.router import _json_default, _to_utc_seconds
from app.db.session import get_db
from app.services.publish_service import (
    apply_delivery_cache_headers,
    compute_etag_from_bytes,
    parse_httpdate,
)
from app.services.site_payload_service import build_site_payload

router = APIRouter(prefix="/delivery/v1", tags=["Delivery"])


@router.get("/sites/{tenant_slug}", summary="Sitio completo publicado (público)")
def get_site_payload(
    tenant_slug: str,
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    if_modified_since: str | None = Header(default=None, alias="If-Modified-Since"),
):
    """
    Devuelve todo el contenido publicado de un sitio en una sola llamada,
    agrupado por bloque. Solo disponible para tenants habilitados; las
    secciones internas (settings, bandejas de mensajes) nunca se exponen.
    """
    payload = build_site_payload(db, tenant_slug)
    if payload is None:
        raise HTTPException(status_code=404, detail="Site not found")

    body_bytes = json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False, default=_json_default
    ).encode("utf-8")
    etag = compute_etag_from_bytes(body_bytes)
    last_modified = _to_utc_seconds(payload.get("published_at"))

    if if_none_match and etag and if_none_match == etag:
        resp = Response(status_code=304)
        apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
        return resp

    if if_modified_since and last_modified:
        ims = _to_utc_seconds(parse_httpdate(if_modified_since))
        if ims and last_modified <= ims:
            resp = Response(status_code=304)
            apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
            return resp

    resp = Response(content=body_bytes, media_type="application/json")
    apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
    return resp
