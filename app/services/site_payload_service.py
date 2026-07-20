from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema
from app.services.delivery_service import strip_internal_delivery_fields

# Tenants allowed to expose a whole-site aggregate. Opt-in on purpose: this
# endpoint returns a tenant's entire published content tree in one public call,
# so no project gets that surface without being listed here.
SITE_PAYLOAD_TENANTS = {"jiribilla"}

# Section keys that must never reach the public site payload.
PRIVATE_SECTION_KEYS = {
    "settings",
    "mensajes",
    "mensajes_eventos",
    "mensajes_bolsa",
}


def _active_schema_dict(db: Session, tenant_id: int, section_id: int) -> dict[str, Any]:
    row = db.scalar(
        select(SectionSchema)
        .where(
            and_(
                SectionSchema.tenant_id == tenant_id,
                SectionSchema.section_id == section_id,
                SectionSchema.is_active.is_(True),
            )
        )
        .order_by(SectionSchema.version.desc())
        .limit(1)
    )
    schema = getattr(row, "schema", None)
    return schema if isinstance(schema, dict) else {}


def is_container_schema(schema: Any) -> bool:
    """A container section spreads its top-level keys as site blocks."""
    if not isinstance(schema, dict):
        return False
    x_ui = schema.get("x-ui")
    return isinstance(x_ui, dict) and x_ui.get("container") is True


def _newer(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    a_aware = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
    b_aware = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return a if a_aware >= b_aware else b


def build_site_payload(db: Session, tenant_slug: str) -> dict[str, Any] | None:
    """
    Whole published site for a tenant, keyed by block.

    Container sections spread their top-level properties as blocks; every other
    section contributes one block under its own section key. Container blocks win
    over a same-named leaf section, so the payload stays stable while a tenant is
    mid-consolidation.

    Returns None when the tenant is not allowlisted, does not exist, or is inactive.
    """
    if tenant_slug not in SITE_PAYLOAD_TENANTS:
        return None

    tenant = db.scalar(
        select(Tenant).where(Tenant.slug == tenant_slug, Tenant.is_active.is_(True))
    )
    if not tenant:
        return None

    rows = db.execute(
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(and_(Entry.tenant_id == tenant.id, Entry.status == "published"))
        .order_by(Entry.id.asc())
    ).all()

    blocks: dict[str, Any] = {}
    container_keys: set[str] = set()
    published_at: datetime | None = None

    for entry, section in rows:
        if section.key in PRIVATE_SECTION_KEYS:
            continue

        data = strip_internal_delivery_fields(entry.data or {})
        if not isinstance(data, dict):
            continue

        if is_container_schema(_active_schema_dict(db, int(tenant.id), int(section.id))):
            for block_key, block_value in data.items():
                blocks[block_key] = block_value
                container_keys.add(block_key)
        elif section.key not in container_keys:
            blocks[section.key] = data

        published_at = _newer(published_at, entry.published_at)
        published_at = _newer(published_at, entry.updated_at)

    return {
        "tenant": {"slug": tenant.slug, "name": tenant.name},
        "published_at": published_at,
        "blocks": blocks,
    }
