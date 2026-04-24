"""
scripts/bootstrap_tenant_settings.py
--------------------------------------
Creates a standardised 'settings' section for a tenant, including its
JSON schema and a draft entry with the contact_email field.

Safe to run multiple times — every step is idempotent:
  - Section already exists → skipped
  - Schema v1 already exists → skipped
  - Entry already exists → skipped

Usage
-----
    # Create settings for one tenant (leave contact_email blank for now):
    python -m scripts.bootstrap_tenant_settings --tenant anro

    # Pre-fill the contact email:
    python -m scripts.bootstrap_tenant_settings --tenant anro --contact-email contact@anro.com

    # The entry is created as DRAFT. After verifying, publish it from the dashboard
    # or pass --publish to publish it immediately:
    python -m scripts.bootstrap_tenant_settings --tenant anro --contact-email contact@anro.com --publish
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema

SECTION_KEY = "settings"
SECTION_NAME = "Site Settings"
SECTION_DESCRIPTION = "Site-wide configuration — editable by the client."

SCHEMA_V1 = {
    "type": "object",
    "title": "Site Settings",
    "properties": {
        "contact_email": {
            "type": "string",
            "format": "email",
            "title": "Contact Form Email",
            "description": "Recipient address for contact form submissions from this site.",
        }
    },
    "required": ["contact_email"],
}


def run(tenant_slug: str, contact_email: str, publish: bool) -> None:
    db = SessionLocal()
    try:
        # 1. Resolve tenant
        tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if not tenant:
            print(f"[ERROR] Tenant '{tenant_slug}' not found.")
            sys.exit(1)

        print(f"Tenant: {tenant.name} (id={tenant.id})")

        # 2. Section
        section = db.scalar(
            select(Section).where(
                Section.tenant_id == tenant.id,
                Section.key == SECTION_KEY,
            )
        )
        if section:
            print(f"  Section '{SECTION_KEY}' already exists (id={section.id}) — skipped.")
        else:
            section = Section(
                tenant_id=tenant.id,
                key=SECTION_KEY,
                name=SECTION_NAME,
                description=SECTION_DESCRIPTION,
            )
            db.add(section)
            db.flush()
            print(f"  Section '{SECTION_KEY}' created (id={section.id}).")

        # 3. Schema v1
        schema_rec = db.scalar(
            select(SectionSchema).where(
                SectionSchema.tenant_id == tenant.id,
                SectionSchema.section_id == section.id,
                SectionSchema.version == 1,
            )
        )
        if schema_rec:
            print(f"  Schema v1 already exists — skipped.")
        else:
            schema_rec = SectionSchema(
                tenant_id=tenant.id,
                section_id=section.id,
                version=1,
                title="Site Settings v1",
                schema=SCHEMA_V1,
                is_active=True,
            )
            db.add(schema_rec)
            db.flush()
            print(f"  Schema v1 created and activated.")

        # 4. Entry
        entry = db.scalar(
            select(Entry).where(
                Entry.tenant_id == tenant.id,
                Entry.section_id == section.id,
                Entry.slug == SECTION_KEY,
            )
        )
        if entry:
            print(f"  Entry already exists (id={entry.id}, status={entry.status}) — skipped.")
        else:
            status = "published" if publish else "draft"
            entry = Entry(
                tenant_id=tenant.id,
                section_id=section.id,
                slug=SECTION_KEY,
                schema_version=1,
                status=status,
                data={"contact_email": contact_email},
            )
            db.add(entry)
            db.flush()
            print(f"  Entry created (id={entry.id}, status={status}).")
            if not publish:
                print(f"  -> Open the dashboard, fill in 'contact_email', and publish the entry.")

        db.commit()
        print("Done.")

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap the 'settings' section for a tenant."
    )
    parser.add_argument("--tenant", required=True, metavar="SLUG", help="Tenant slug.")
    parser.add_argument(
        "--contact-email", default="", metavar="EMAIL",
        help="Pre-fill contact_email in the entry data (optional).",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help="Publish the entry immediately (default: leave as draft).",
    )
    args = parser.parse_args()
    run(args.tenant, args.contact_email, args.publish)


if __name__ == "__main__":
    main()
