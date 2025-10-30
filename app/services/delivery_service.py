# app/services/delivery_service.py
from __future__ import annotations
from typing import Tuple, List
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.models.content import Entry, Section
from app.models.auth import Tenant  # ajusta si tu Tenant vive en otro módulo
from app.schemas.delivery import DeliveryEntryOut

def _base_published_query():
    return (
        select(Entry)
        .join(Section, Section.id == Entry.section_id)
        .join(Tenant, Tenant.id == Entry.tenant_id)
        .where(Entry.status == "published")
    )

def fetch_published_entries(
    db: Session,
    tenant_slug: str,
    section_key: str | None,
    slug: str | None,
    limit: int,
    offset: int,
) -> Tuple[List[DeliveryEntryOut], int, str | None]:
    q = _base_published_query().where(Tenant.slug == tenant_slug)
    cnt = _base_published_query().where(Tenant.slug == tenant_slug)

    if section_key:
        q = q.where(Section.key == section_key)
        cnt = cnt.where(Section.key == section_key)
    if slug:
        q = q.where(Entry.slug == slug)
        cnt = cnt.where(Entry.slug == slug)

    total = db.scalar(select(func.count()).select_from(cnt.subquery()))

    q = q.order_by(Entry.published_at.desc().nullslast(), Entry.updated_at.desc().nullslast()) \
         .limit(limit).offset(offset)
    rows = db.scalars(q).all()

    items = [
        DeliveryEntryOut(
            id=e.id,
            tenant_id=e.tenant_id,
            section_id=e.section_id,
            slug=e.slug,
            status=e.status,
            schema_version=e.schema_version,
            data=e.data,
            updated_at=e.updated_at,
            published_at=e.published_at,
        )
        for e in rows
    ]

    # ETag simple de lista: hash de (tenant|section|slug|total|max_ts)
    etag = None
    try:
        import hashlib
        max_updated = max((i.updated_at for i in items if i.updated_at), default=None)
        max_published = max((i.published_at for i in items if i.published_at), default=None)
        key = f"{tenant_slug}|{section_key or ''}|{slug or ''}|{total}|{max_updated or ''}|{max_published or ''}"
        etag = hashlib.sha256(key.encode("utf-8")).hexdigest()
    except Exception:
        etag = None

    return items, int(total or 0), etag


def fetch_single_published_entry(
    db: Session,
    tenant_slug: str,
    section_key: str,
    slug: str,
):
    q = (
        _base_published_query()
        .where(Tenant.slug == tenant_slug)
        .where(Section.key == section_key)
        .where(Entry.slug == slug)
        .limit(1)
    )
    return db.scalars(q).first()
