# app/api/delivery/router.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Header, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.delivery import DeliveryEntryOut, DeliveryEntryListOut
from app.services.delivery_service import (
    fetch_published_entries,
    fetch_single_published_entry,
)
from app.services.publish_service import (
    parse_httpdate,
    compute_etag_from_bytes,
    apply_delivery_cache_headers,
)

router = APIRouter(prefix="/delivery/v1", tags=["Delivery"])


def _to_utc_seconds(dt: datetime | None) -> datetime | None:
    """
    Normaliza un datetime a UTC y sin microsegundos (precisión de segundos),
    adecuado para comparaciones con If-Modified-Since.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0)


def _max_last_modified_from_items(items: Iterable[DeliveryEntryOut]) -> Optional[datetime]:
    """
    Para listados: el Last-Modified será el máximo entre published_at y updated_at
    de los ítems devueltos, normalizado a UTC (segundos).
    """
    last: Optional[datetime] = None
    for it in items:
        # items pueden ser DeliveryEntryOut (obj) o dict; toleramos ambos
        pub = getattr(it, "published_at", None) if hasattr(it, "published_at") else (it.get("published_at") if isinstance(it, dict) else None)
        upd = getattr(it, "updated_at", None) if hasattr(it, "updated_at") else (it.get("updated_at") if isinstance(it, dict) else None)

        for cand in (pub, upd):
            cand_utc = _to_utc_seconds(cand)
            if cand_utc and (last is None or cand_utc > last):
                last = cand_utc
    return last


@router.get(
    "/entries",
    response_model=DeliveryEntryListOut,
    summary="Listar entries publicados (público)",
)
def list_published_entries(
    request: Request,
    tenant_slug: str = Query(..., description="Slug del tenant"),
    section_key: str | None = Query(None, description="Clave de sección (opcional)"),
    slug: str | None = Query(None, description="Slug exacto (opcional)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    if_modified_since: str | None = Header(default=None, alias="If-Modified-Since"),
):
    """
    Lista solo entries en estado 'published'. Aplica:
    - ETag (If-None-Match → 304, prioridad sobre If-Modified-Since)
    - Last-Modified (If-Modified-Since → 304)
    - Cache-Control específico para listados
    """
    items, total, _etag_legacy = fetch_published_entries(
        db=db,
        tenant_slug=tenant_slug,
        section_key=section_key,
        slug=slug,
        limit=limit,
        offset=offset,
    )

    # Construimos el cuerpo de salida según el schema público
    out = DeliveryEntryListOut(total=total, limit=limit, offset=offset, items=items)

    # Serializamos para calcular ETag estable
    out_dict = out.model_dump(by_alias=True, exclude_none=True, mode="json")
    body_bytes = json.dumps(out_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    etag = compute_etag_from_bytes(body_bytes)

    # Last-Modified (máximo de los ítems), normalizado a UTC (segundos)
    last_modified = _max_last_modified_from_items(items)

    # 1) If-None-Match (ETag) tiene prioridad
    if if_none_match and etag and if_none_match == etag:
        resp = Response(status_code=304)
        apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
        return resp

    # 2) If-Modified-Since (si llegó y tenemos last_modified)
    if if_modified_since and last_modified:
        ims = parse_httpdate(if_modified_since)
        ims = _to_utc_seconds(ims)
        # Si el recurso NO ha cambiado desde ims → 304
        if ims and last_modified <= ims:
            resp = Response(status_code=304)
            apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
            return resp

    # Respuesta 200 con headers de caché avanzados
    resp = Response(content=body_bytes, media_type="application/json")
    apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
    return resp


@router.get(
    "/tenants/{tenant_slug}/sections/{section_key}/entries/{slug}",
    response_model=DeliveryEntryOut,
    summary="Obtener entry publicado (público)",
)
def get_published_entry(
    request: Request,
    tenant_slug: str,
    section_key: str,
    slug: str,
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    if_modified_since: str | None = Header(default=None, alias="If-Modified-Since"),
):
    """
    Devuelve un entry publicado. Aplica:
    - ETag (If-None-Match → 304, prioridad sobre If-Modified-Since)
    - Last-Modified (If-Modified-Since → 304)
    - Cache-Control específico para detalle
    """
    entry = fetch_single_published_entry(db, tenant_slug, section_key, slug)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found or not published")

    # Armamos el payload público
    out = DeliveryEntryOut(
        id=entry.id,
        tenant_id=entry.tenant_id,
        section_id=entry.section_id,
        slug=entry.slug,
        status=entry.status,
        schema_version=entry.schema_version,
        data=entry.data,
        updated_at=entry.updated_at,
        published_at=entry.published_at,
    )

    # Serializamos para ETag estable
    out_dict = out.model_dump(by_alias=True, exclude_none=True, mode="json")
    body_bytes = json.dumps(out_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    etag = compute_etag_from_bytes(body_bytes)

    # Last-Modified del detalle: published_at (si existe) o updated_at, normalizado a UTC (segundos)
    last_modified: datetime | None = _to_utc_seconds(entry.published_at or entry.updated_at)

    # 1) If-None-Match (prioridad)
    if if_none_match and etag and if_none_match == etag:
        resp = Response(status_code=304)
        apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=True)
        return resp

    # 2) If-Modified-Since
    if if_modified_since and last_modified:
        ims = parse_httpdate(if_modified_since)
        ims = _to_utc_seconds(ims)
        if ims and last_modified <= ims:
            resp = Response(status_code=304)
            apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=True)
            return resp

    # 200 con headers
    resp = Response(content=body_bytes, media_type="application/json")
    apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=True)
    return resp


