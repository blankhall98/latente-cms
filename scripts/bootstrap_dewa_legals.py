from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.auth import Tenant  # noqa: E402
from app.models.content import Entry, Section, SectionSchema  # noqa: E402
from app.services.content_service import create_section, set_active_schema  # noqa: E402


TENANT_SLUG = "dewa"
SECTION_KEY = "legals"
SECTION_NAME = "Legals"
ENTRY_SLUG = "legals"
SCHEMA_VERSION = 1
SCHEMA_PATH = ROOT / "app" / "schemas" / "dewa" / "legals" / "v1.json"
CONTENT_PATH = ROOT / "content" / "dewa" / "legals_v1.json"


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.as_posix()} must contain a JSON object.")
    return payload


def _get_dewa_tenant(db: Session) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
    if tenant is None:
        raise RuntimeError("DEWA tenant not found. Expected tenant slug 'dewa'.")
    return tenant


def _upsert_legals_schema(db: Session, *, tenant_id: int, schema_obj: dict) -> Section:
    section = create_section(db, tenant_id=tenant_id, key=SECTION_KEY, name=SECTION_NAME)
    db.flush()

    schema_rec = db.scalar(
        select(SectionSchema).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section.id,
            SectionSchema.version == SCHEMA_VERSION,
        )
    )
    if schema_rec is None:
        schema_rec = SectionSchema(
            tenant_id=tenant_id,
            section_id=section.id,
            version=SCHEMA_VERSION,
            title=f"{SECTION_KEY}@{SCHEMA_VERSION}",
            schema=schema_obj,
            is_active=False,
        )
        db.add(schema_rec)
    else:
        schema_rec.title = f"{SECTION_KEY}@{SCHEMA_VERSION}"
        schema_rec.schema = schema_obj

    db.flush()
    set_active_schema(
        db,
        tenant_id=tenant_id,
        section_id=int(section.id),
        version=SCHEMA_VERSION,
    )
    return section


def _ensure_legals_entry(
    db: Session,
    *,
    tenant_id: int,
    section_id: int,
    content_obj: dict,
    replace_content: bool,
    publish: bool,
) -> Entry:
    entry = db.scalar(
        select(Entry).where(
            Entry.tenant_id == tenant_id,
            Entry.section_id == section_id,
            Entry.slug == ENTRY_SLUG,
        )
    )
    if entry is None:
        entry = Entry(
            tenant_id=tenant_id,
            section_id=section_id,
            slug=ENTRY_SLUG,
            schema_version=SCHEMA_VERSION,
            status="draft",
            data=content_obj,
        )
        db.add(entry)
        db.flush()
    elif replace_content:
        entry.schema_version = SCHEMA_VERSION
        entry.data = content_obj

    if publish:
        entry.status = "published"
        entry.published_at = db.scalar(select(func.now()))

    db.flush()
    return entry


def run(*, dry_run: bool = False, publish: bool = False, replace_content: bool = False) -> None:
    schema_obj = _load_json(SCHEMA_PATH)
    content_obj = _load_json(CONTENT_PATH)

    Draft202012Validator.check_schema(schema_obj)
    Draft202012Validator(schema_obj).validate(content_obj)

    db = SessionLocal()
    try:
        tenant = _get_dewa_tenant(db)
        section = _upsert_legals_schema(db, tenant_id=int(tenant.id), schema_obj=schema_obj)
        entry = _ensure_legals_entry(
            db,
            tenant_id=int(tenant.id),
            section_id=int(section.id),
            content_obj=content_obj,
            replace_content=replace_content,
            publish=publish,
        )

        if dry_run:
            db.rollback()
            print("[DRY-RUN] No database changes committed.")
        else:
            db.commit()
            print("[OK] DEWA legals schema and entry ensured.")

        print(f"Tenant: {tenant.slug} ({tenant.id})")
        print(f"Section: {SECTION_KEY} ({section.id})")
        print(f"Entry: {ENTRY_SLUG} ({entry.id}) status={entry.status}")
        print(f"Delivery URL: /delivery/v1/tenants/{tenant.slug}/sections/{SECTION_KEY}/entries/{ENTRY_SLUG}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create/update only the DEWA Legals CMS section and initial entry."
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and preview changes without committing")
    parser.add_argument("--publish", action="store_true", help="publish the legals entry after ensuring it")
    parser.add_argument(
        "--replace-content",
        action="store_true",
        help="overwrite existing legals entry data with content/dewa/legals_v1.json",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, publish=args.publish, replace_content=args.replace_content)


if __name__ == "__main__":
    main()
