# scripts/seed_tenant_content.py
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Section, SectionSchema, Entry

# Optional validation with jsonschema (Draft 2020-12)
try:
    from jsonschema import Draft202012Validator
    HAS_JSONSCHEMA = True
except Exception:
    HAS_JSONSCHEMA = False


def _get_db() -> Session:
    return SessionLocal()


def _load_json_from_file(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Content file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_tenant(db: Session, tenant_key_or_name: str) -> Tenant:
    t = db.scalar(
        select(Tenant).where(
            (Tenant.slug == tenant_key_or_name) | (Tenant.name == tenant_key_or_name)
        )
    )
    if not t:
        raise RuntimeError(f"Tenant not found: {tenant_key_or_name}")
    return t


def _get_section(db: Session, tenant_id: int, section_key: str) -> Section:
    s = db.scalar(
        select(Section).where(
            Section.tenant_id == tenant_id,
            Section.key == section_key,
        )
    )
    if not s:
        raise RuntimeError(
            f"Section not found for tenant_id={tenant_id} and key='{section_key}'. "
            f"Did you run scripts.seed_tenant_schemas first?"
        )
    return s


def _resolve_schema_version(db: Session, tenant_id: int, section_id: int, explicit_version: int | None) -> int:
    if explicit_version is not None:
        return int(explicit_version)

    # Prefer active version
    v = db.scalar(
        select(SectionSchema.version).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.is_active.is_(True),
        ).limit(1)
    )
    if v is not None:
        return int(v)

    # Fallback to highest
    v = db.scalar(
        select(SectionSchema.version).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
        ).order_by(desc(SectionSchema.version)).limit(1)
    )
    if v is None:
        raise RuntimeError("No schema versions found for this section; seed schemas first.")
    return int(v)


def _get_schema_json(db: Session, tenant_id: int, section_id: int, version: int) -> dict | None:
    rec = db.scalar(
        select(SectionSchema).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.version == version,
        )
    )
    if not rec:
        return None
    try:
        schema_obj = rec.schema  # ORM attr for JSONB column
    except Exception:
        schema_obj = getattr(rec, "json_schema", None)
    return schema_obj


def _validate_content_against_schema(schema_obj: dict | None, content_obj: dict) -> None:
    if not schema_obj or not HAS_JSONSCHEMA:
        return
    Draft202012Validator.check_schema(schema_obj)
    Draft202012Validator(schema_obj).validate(content_obj)


def _get_or_create_entry(
    db: Session,
    tenant_id: int,
    section_id: int,
    slug: str,
    schema_version: int,
    content_obj: dict,
) -> Entry:
    """
    IMPORTANT: when creating a brand new Entry, we must set BOTH schema_version and data
    BEFORE the first flush, because both columns are NOT NULL in your DB.
    """
    e = db.scalar(
        select(Entry).where(
            Entry.tenant_id == tenant_id,
            Entry.section_id == section_id,
            Entry.slug == slug,
        )
    )
    if e:
        # keep existing, caller will update .data and .schema_version
        return e

    e = Entry(
        tenant_id=tenant_id,
        section_id=section_id,
        slug=slug,
        schema_version=schema_version,  # NOT NULL
        status="draft",
        data=content_obj,               # NOT NULL
    )
    db.add(e)
    db.flush()  # safe now: both NOT NULL fields are set
    return e


def _publish_entry(db: Session, e: Entry, replace: bool = False) -> None:
    e.status = "published"
    e.published_at = db.scalar(select(func.now())) or datetime.now(timezone.utc)
    # 'replace' flag is a no-op here; keep for future webhook/delivery semantics.


def run(
    tenant_key_or_name: str,
    section_key: str,
    slug: str,
    content_path: str,
    schema_version_cli: int | None = None,
    publish: bool = False,
    replace: bool = False,
) -> None:
    db = _get_db()
    try:
        t = _get_tenant(db, tenant_key_or_name)
        s = _get_section(db, t.id, section_key)
        schema_version = _resolve_schema_version(db, t.id, s.id, schema_version_cli)

        print(
            f"[INFO] Tenant='{t.slug}' Section='{s.key}' "
            f"Slug='{slug}' schema_version={schema_version}"
        )

        content_obj = _load_json_from_file(content_path)

        # Optional: validate against registered JSON Schema
        schema_obj = _get_schema_json(db, t.id, s.id, schema_version)
        try:
            _validate_content_against_schema(schema_obj, content_obj)
        except Exception as ve:
            raise RuntimeError(f"JSON content failed validation: {ve}") from ve

        # Create or get entry (new path sets data+schema_version before flush)
        e = _get_or_create_entry(db, t.id, s.id, slug, schema_version, content_obj)

        # Update path: ensure both fields are set
        e.schema_version = schema_version
        e.data = content_obj

        db.commit()
        print(f"[OK] Upserted entry id={e.id} status={e.status}")

        if publish:
            _publish_entry(db, e, replace=replace)
            db.commit()
            print("[OK] Published.")

        print(
            "Delivery detail URL:\n"
            f"  /delivery/v1/tenants/{t.slug}/sections/{s.key}/entries/{slug}"
        )

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser(
        description="Seed (create/update) a content entry for a tenant/section/slug"
    )
    p.add_argument("tenant", help="Tenant slug or name (e.g., 'anro')")
    p.add_argument("section_key", help="Section key (e.g., 'home')")
    p.add_argument("slug", help="Entry slug (e.g., 'home')")
    p.add_argument("content_path", help="Path to JSON content file (e.g., content/anro/home_v1.json)")
    p.add_argument("--schema-version", type=int, default=None, help="Schema version to use; defaults to active")
    p.add_argument("--publish", action="store_true", help="Publish after upsert")
    p.add_argument("--replace", action="store_true", help="Pass a replace flag (reserved)")
    args = p.parse_args()

    run(
        args.tenant,
        args.section_key,
        args.slug,
        args.content_path,
        args.schema_version,
        args.publish,
        args.replace,
    )


if __name__ == "__main__":
    main()



