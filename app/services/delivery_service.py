# app/services/delivery_service.py
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.models.auth import Tenant
from app.models.content import Entry, Section
from app.schemas.delivery import DeliveryEntryOut

INTERNAL_DELIVERY_KEYS = {"__draft"}


def strip_internal_delivery_fields(value: Any) -> Any:
    """
    Remove CMS-only fields from public Delivery payloads.

    Admin stores unpublished edits under data.__draft. Public delivery must
    keep returning the published data shape without exposing that draft branch.
    """
    if isinstance(value, dict):
        return {
            key: strip_internal_delivery_fields(item)
            for key, item in value.items()
            if key not in INTERNAL_DELIVERY_KEYS
        }
    if isinstance(value, list):
        return [strip_internal_delivery_fields(item) for item in value]
    return value


try:
    from app.models.content import EntryVersion

    HAS_ENTRY_VERSION = True
except Exception:  # pragma: no cover
    EntryVersion = None  # type: ignore
    HAS_ENTRY_VERSION = False


def _base_published_query():
    """
    Entries with status='published'. Useful for list endpoints.
    Detail endpoints prefer the persisted publish snapshot when available.
    """
    return (
        select(Entry)
        .join(Section, Section.id == Entry.section_id)
        .join(Tenant, Tenant.id == Entry.tenant_id)
        .where(Entry.status == "published")
    )


def _as_db_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def _latest_published_snapshot(
    db: Session,
    entry_id: int,
    *,
    not_older_than: datetime | None = None,
) -> Optional[dict]:
    """
    Return the latest persisted publish snapshot for an entry.

    This uses the existing entry_versions table, so it does not require a
    production database migration. It is the stable public payload created at
    publish time, before later draft edits are stored.
    """
    if not HAS_ENTRY_VERSION:
        return None

    q = select(EntryVersion).where(EntryVersion.entry_id == entry_id)
    q_pub = q.where(EntryVersion.reason.in_(["publish", "PUBLISH", "published"]))

    row = db.scalars(q_pub.order_by(desc(EntryVersion.version_idx), desc(EntryVersion.id))).first()
    if row and getattr(row, "data", None):
        snapshot_created_at = _as_db_naive(getattr(row, "created_at", None))
        min_created_at = _as_db_naive(not_older_than)
        if (
            snapshot_created_at is not None
            and min_created_at is not None
            and snapshot_created_at < min_created_at
        ):
            return None
        return dict(row.data)

    return None


def _effective_published_payload(db: Session, entry: Entry) -> Optional[dict]:
    """
    Return the public payload for an entry.

    Prefer the publish snapshot so Delivery stays stable after later draft
    edits. Fallback to Entry.data for old published entries that predate
    snapshots.
    """
    snap = _latest_published_snapshot(db, int(entry.id))
    if isinstance(snap, dict) and snap:
        return snap

    data_val = getattr(entry, "data", None)
    if (getattr(entry, "status", None) == "published") and isinstance(data_val, dict) and data_val:
        return data_val

    return None


def _first_ragni_gallery_image(project: dict) -> dict:
    gallery = project.get("heroGallery")
    if not isinstance(gallery, list):
        return {}
    for item in gallery:
        if isinstance(item, dict) and item.get("url"):
            return dict(item)
    return {}


def _enrich_ragni_home_featured_projects(
    db: Session,
    *,
    tenant_slug: str,
    section_key: str,
    slug: str,
    data: dict,
) -> dict:
    """
    Ragni-Grady stores Home featured project selections as project titles for the
    admin picker. Public delivery expands those selections into the project
    fields requested by the frontend.
    """
    if (tenant_slug or "").strip().lower() != "ragni-grady":
        return data
    if section_key != "home" or slug != "home":
        return data
    selections = data.get("featuredProjects")
    if not isinstance(selections, list):
        return data

    selected_titles = [str(item).strip() for item in selections if str(item).strip()]
    out = dict(data)
    out["featuredProjectTitles"] = selected_titles
    if not selected_titles:
        out["featuredProjects"] = []
        return out

    projects_entry = db.scalars(
        select(Entry)
        .join(Section, Section.id == Entry.section_id)
        .join(Tenant, Tenant.id == Entry.tenant_id)
        .where(
            and_(
                Tenant.slug == "ragni-grady",
                Section.key == "projects",
                Entry.slug == "projects",
            )
        )
        .limit(1)
    ).first()
    projects_payload = _effective_published_payload(db, projects_entry) if projects_entry else None
    projects = projects_payload.get("projects") if isinstance(projects_payload, dict) else []

    by_title: dict[str, dict] = {}
    if isinstance(projects, list):
        for project in projects:
            if not isinstance(project, dict):
                continue
            title = str(project.get("title") or project.get("projectTitle") or "").strip()
            if title and title not in by_title:
                by_title[title] = project

    featured = []
    for title in selected_titles:
        project = by_title.get(title)
        if not isinstance(project, dict):
            featured.append(
                {
                    "projectTitle": title,
                    "projectHeroDescription": "",
                    "location": "",
                    "heroImage": {},
                }
            )
            continue
        featured.append(
            {
                "projectTitle": str(project.get("title") or title),
                "projectHeroDescription": str(project.get("heroDescription") or ""),
                "location": str(project.get("location") or ""),
                "heroImage": _first_ragni_gallery_image(project),
            }
        )

    out["featuredProjects"] = featured
    return out


def _normalize_ragni_portfolio_data(
    *,
    tenant_slug: str,
    section_key: str,
    slug: str,
    data: dict,
) -> dict:
    if (tenant_slug or "").strip().lower() != "ragni-grady":
        return data
    if section_key != "portfolio" or slug != "portfolio":
        return data

    disciplines = data.get("disciplines")
    if not isinstance(disciplines, dict):
        return data

    out = dict(data)
    normalized_disciplines: dict[str, Any] = {}
    for key, value in disciplines.items():
        if key in ("discipline1", "discipline2") and isinstance(value, dict):
            normalized_disciplines[key] = {
                "disciplineTitle": value.get("disciplineTitle", ""),
                "disciplineText": value.get("disciplineText", ""),
            }
        else:
            normalized_disciplines[key] = value
    out["disciplines"] = normalized_disciplines
    return out


def fetch_published_entries(
    db: Session,
    tenant_slug: str,
    section_key: str | None,
    slug: str | None,
    limit: int,
    offset: int,
) -> Tuple[List[DeliveryEntryOut], int, str | None]:
    """
    Public list endpoint. It intentionally keeps the current behavior:
    only entries with status='published' are listed.
    """
    q = _base_published_query().where(Tenant.slug == tenant_slug)
    cnt = _base_published_query().where(Tenant.slug == tenant_slug)

    if section_key:
        q = q.where(Section.key == section_key)
        cnt = cnt.where(Section.key == section_key)
    if slug:
        q = q.where(Entry.slug == slug)
        cnt = cnt.where(Entry.slug == slug)

    total = db.scalar(select(func.count()).select_from(cnt.subquery())) or 0

    q = (
        q.order_by(
            Entry.published_at.desc().nullslast(),
            Entry.updated_at.desc().nullslast(),
            Entry.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = db.scalars(q).all()

    items: List[DeliveryEntryOut] = []
    for e in rows:
        data_payload = strip_internal_delivery_fields(e.data or {})
        if section_key == "home" and e.slug == "home" and isinstance(data_payload, dict):
            data_payload = _enrich_ragni_home_featured_projects(
                db,
                tenant_slug=tenant_slug,
                section_key="home",
                slug=e.slug,
                data=data_payload,
            )
        if section_key == "portfolio" and e.slug == "portfolio" and isinstance(data_payload, dict):
            data_payload = _normalize_ragni_portfolio_data(
                tenant_slug=tenant_slug,
                section_key="portfolio",
                slug=e.slug,
                data=data_payload,
            )

        items.append(
            DeliveryEntryOut(
                id=e.id,
                tenant_id=e.tenant_id,
                section_id=e.section_id,
                slug=e.slug,
                status=e.status,
                schema_version=e.schema_version,
                data=data_payload,
                updated_at=e.updated_at,
                published_at=getattr(e, "published_at", None),
            )
        )

    etag = None
    try:
        max_updated = max((i.updated_at for i in items if i.updated_at), default=None)
        max_published = max((i.published_at for i in items if i.published_at), default=None)
        key = f"{tenant_slug}|{section_key or ''}|{slug or ''}|{total}|{max_updated or ''}|{max_published or ''}"
        etag = hashlib.sha256(key.encode("utf-8")).hexdigest()
    except Exception:
        etag = None

    return items, int(total), etag


def fetch_single_published_entry(
    db: Session,
    tenant_slug: str,
    section_key: str,
    slug: str,
) -> Optional[DeliveryEntryOut]:
    """
    Public detail endpoint.

    It first reads entry metadata without Entry.data. If a publish snapshot
    exists, Delivery uses that snapshot; otherwise it falls back to Entry.data
    for old published rows.
    """
    row = db.execute(
        select(
            Entry.id,
            Entry.tenant_id,
            Entry.section_id,
            Entry.slug,
            Entry.status,
            Entry.schema_version,
            Entry.updated_at,
            Entry.published_at,
        )
        .join(Section, Section.id == Entry.section_id)
        .join(Tenant, Tenant.id == Entry.tenant_id)
        .where(
            and_(
                Tenant.slug == tenant_slug,
                Section.key == section_key,
                Entry.slug == slug,
            )
        )
        .limit(1)
    ).mappings().first()

    if not row:
        return None

    effective = _latest_published_snapshot(
        db,
        int(row["id"]),
        not_older_than=row["updated_at"],
    )
    if not isinstance(effective, dict) or not effective:
        if row["status"] != "published":
            return None
        effective = db.scalar(select(Entry.data).where(Entry.id == row["id"]))

    if not isinstance(effective, dict) or not effective:
        return None

    data_payload = strip_internal_delivery_fields(effective)
    if isinstance(data_payload, dict):
        data_payload = _enrich_ragni_home_featured_projects(
            db,
            tenant_slug=tenant_slug,
            section_key=section_key,
            slug=slug,
            data=data_payload,
        )
        data_payload = _normalize_ragni_portfolio_data(
            tenant_slug=tenant_slug,
            section_key=section_key,
            slug=slug,
            data=data_payload,
        )

    return DeliveryEntryOut(
        id=row["id"],
        tenant_id=row["tenant_id"],
        section_id=row["section_id"],
        slug=row["slug"],
        status="published",
        schema_version=row["schema_version"],
        data=data_payload,
        updated_at=row["updated_at"],
        published_at=row["published_at"],
    )
