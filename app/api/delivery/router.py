# app/api/delivery/router.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Response, Header
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.delivery import DeliveryEntryOut, DeliveryEntryListOut
from app.services.delivery_service import (
    fetch_published_entries,
    fetch_single_published_entry,
)
from app.services.publish_service import compute_etag, apply_cache_headers

router = APIRouter(prefix="/delivery/v1", tags=["Delivery"])

@router.get(
    "/entries",
    response_model=DeliveryEntryListOut,
    summary="Listar entries publicados (público)",
)
def list_published_entries(
    tenant_slug: str = Query(..., description="Slug del tenant"),
    section_key: str | None = Query(None, description="Clave de sección (opcional)"),
    slug: str | None = Query(None, description="Slug exacto (opcional)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    response: Response = None,
):
    items, total, etag = fetch_published_entries(
        db=db,
        tenant_slug=tenant_slug,
        section_key=section_key,
        slug=slug,
        limit=limit,
        offset=offset,
    )

    if if_none_match and etag and if_none_match == etag:
        resp = Response(status_code=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp

    out = DeliveryEntryListOut(total=total, limit=limit, offset=offset, items=items)
    response.headers["Cache-Control"] = "public, max-age=60"
    if etag:
        response.headers["ETag"] = etag
    return out


@router.get(
    "/tenants/{tenant_slug}/sections/{section_key}/entries/{slug}",
    response_model=DeliveryEntryOut,
    summary="Obtener entry publicado (público)",
)
def get_published_entry(
    tenant_slug: str,
    section_key: str,
    slug: str,
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    response: Response = None,
):
    entry = fetch_single_published_entry(db, tenant_slug, section_key, slug)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found or not published")

    etag = compute_etag(entry)
    if if_none_match and if_none_match == etag:
        resp = Response(status_code=304)
        apply_cache_headers(resp, status=entry.status)  # published -> público
        resp.headers["ETag"] = etag
        return resp

    apply_cache_headers(response, status=entry.status)
    response.headers["ETag"] = etag

    return DeliveryEntryOut(
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
