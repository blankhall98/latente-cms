from __future__ import annotations

"""
Bootstrap Jiribilla in an existing database.

Safe to run multiple times:
  - tenant is created or reused by slug/name
  - schemas are loaded from app/schemas/jiribilla
  - content entries are upserted and published
  - settings entry is created and published with hola@jiribilla.studio

Usage:
    python -m scripts.bootstrap_jiribilla
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.auth import Tenant  # noqa: E402
from app.models.content import Entry, Section, SectionSchema  # noqa: E402
from scripts.bootstrap_tenant_settings import run as seed_tenant_settings  # noqa: E402
from scripts.seed_tenant_content import run as seed_tenant_content  # noqa: E402
from scripts.seed_tenant_schemas import run as seed_tenant_schemas  # noqa: E402


TENANT_NAME = "Jiribilla"
TENANT_SLUG = "jiribilla"
CONTACT_EMAIL = "hola@jiribilla.studio"

SECTIONS = [
    "hero",
    "mesa_uno",
    "proyectos",
    "eventos_privados",
    "glosario",
    "equipo",
    "footer",
    "social_links",
    "forms",
    "privacy_policy",
]

SECTION_LABELS = {
    "hero": "Hero",
    "mesa_uno": "Mesa Uno",
    "proyectos": "Proyectos",
    "eventos_privados": "Eventos Privados",
    "glosario": "Glosario",
    "equipo": "Equipo",
    "footer": "Footer",
    "social_links": "Social and Links",
    "forms": "Forms",
    "privacy_policy": "Privacy Policy",
}

# Inbox sections rendered by the custom dashboard view (no schema file / seed content).
INBOX_SECTIONS = {
    "mensajes_eventos": "Mensajes: Eventos Privados",
    "mensajes_bolsa": "Mensajes: Bolsa de Trabajo",
}

# Per-form destination addresses, editable from the dashboard Settings page.
SETTINGS_FORM_EMAIL_FIELDS = {
    "eventos_email": {
        "type": "string",
        "title": "Eventos Privados — Destination Email",
        "description": "Recipient for private-event form submissions. Falls back to Contact Form Email when blank.",
    },
    "bolsa_trabajo_email": {
        "type": "string",
        "title": "Bolsa de Trabajo — Destination Email",
        "description": "Recipient for job-application form submissions. Falls back to Contact Form Email when blank.",
    },
}


def _ensure_tenant() -> Tenant:
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(
            select(Tenant).where(
                or_(Tenant.slug == TENANT_SLUG, Tenant.name == TENANT_NAME)
            )
        )
        if tenant is None:
            tenant = Tenant(name=TENANT_NAME, slug=TENANT_SLUG)
            db.add(tenant)
            db.flush()
            print(f"[jiribilla] Tenant created: id={tenant.id} slug={tenant.slug}")
        else:
            print(f"[jiribilla] Tenant reused: id={tenant.id} slug={tenant.slug}")

        if tenant.name != TENANT_NAME or tenant.slug != TENANT_SLUG:
            tenant.name = TENANT_NAME
            tenant.slug = TENANT_SLUG

        db.commit()
        db.refresh(tenant)
        return tenant
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _seed_content() -> None:
    for section in SECTIONS:
        content_path = f"content/{TENANT_SLUG}/{section}_v1.json"
        seed_tenant_content(
            tenant_key_or_name=TENANT_SLUG,
            section_key=section,
            slug=section,
            content_path=content_path,
            schema_version_cli=None,
            publish=True,
            replace=False,
        )


def _sync_section_names() -> None:
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if tenant is None:
            raise RuntimeError(f"Tenant not found: {TENANT_SLUG}")

        for key, label in SECTION_LABELS.items():
            section = db.scalar(
                select(Section).where(
                    Section.tenant_id == tenant.id,
                    Section.key == key,
                )
            )
            if section is not None and section.name != label:
                section.name = label

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_settings_form_emails() -> None:
    """Add eventos_email / bolsa_trabajo_email to the settings schema + entry (idempotent)."""
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if tenant is None:
            raise RuntimeError(f"Tenant not found: {TENANT_SLUG}")

        section = db.scalar(
            select(Section).where(Section.tenant_id == tenant.id, Section.key == "settings")
        )
        if section is None:
            raise RuntimeError("Jiribilla settings section not found — run bootstrap first.")

        schema_rec = db.scalar(
            select(SectionSchema).where(
                SectionSchema.tenant_id == tenant.id,
                SectionSchema.section_id == section.id,
                SectionSchema.version == 1,
            )
        )
        if schema_rec is not None:
            schema = dict(schema_rec.schema or {})
            properties = dict(schema.get("properties") or {})
            changed = False
            for key, definition in SETTINGS_FORM_EMAIL_FIELDS.items():
                if key not in properties:
                    properties[key] = dict(definition)
                    changed = True
            if changed:
                schema["properties"] = properties
                schema_rec.schema = schema
                print("[jiribilla] settings schema: added form email fields")

        entry = db.scalar(
            select(Entry).where(
                Entry.tenant_id == tenant.id,
                Entry.section_id == section.id,
                Entry.slug == "settings",
            )
        )
        if entry is not None:
            data = dict(entry.data or {})
            changed = False
            for key in SETTINGS_FORM_EMAIL_FIELDS:
                if key not in data:
                    data[key] = ""
                    changed = True
            if changed:
                entry.data = data
                print("[jiribilla] settings entry: seeded empty form email fields")

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_inbox_sections() -> None:
    """Create the two message-inbox sections + minimal schema + published entry (idempotent)."""
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if tenant is None:
            raise RuntimeError(f"Tenant not found: {TENANT_SLUG}")

        for key, label in INBOX_SECTIONS.items():
            section = db.scalar(
                select(Section).where(Section.tenant_id == tenant.id, Section.key == key)
            )
            if section is None:
                section = Section(
                    tenant_id=tenant.id,
                    key=key,
                    name=label,
                    description="Form submissions inbox — read-only view.",
                )
                db.add(section)
                db.flush()
                print(f"[jiribilla] Section created: {key}")
            elif section.name != label:
                section.name = label

            schema_rec = db.scalar(
                select(SectionSchema).where(
                    SectionSchema.tenant_id == tenant.id,
                    SectionSchema.section_id == section.id,
                    SectionSchema.version == 1,
                )
            )
            if schema_rec is None:
                db.add(
                    SectionSchema(
                        tenant_id=tenant.id,
                        section_id=section.id,
                        version=1,
                        title=f"{label} v1",
                        schema={
                            "type": "object",
                            "title": label,
                            "properties": {"title": {"type": "string", "title": "Title"}},
                        },
                        is_active=True,
                    )
                )
                print(f"[jiribilla] Schema v1 created: {key}")

            entry = db.scalar(
                select(Entry).where(
                    Entry.tenant_id == tenant.id,
                    Entry.section_id == section.id,
                    Entry.slug == key,
                )
            )
            if entry is None:
                db.add(
                    Entry(
                        tenant_id=tenant.id,
                        section_id=section.id,
                        slug=key,
                        schema_version=1,
                        status="published",
                        data={"title": label},
                    )
                )
                print(f"[jiribilla] Entry created: {key}")

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run() -> None:
    tenant = _ensure_tenant()
    seed_tenant_schemas(
        tenant_key_or_name=TENANT_SLUG,
        base_dir="app/schemas",
        set_active=[],
        dry_run=False,
    )
    _sync_section_names()
    _seed_content()
    seed_tenant_settings(
        tenant_slug=TENANT_SLUG,
        contact_email=CONTACT_EMAIL,
        publish=True,
    )
    _ensure_settings_form_emails()
    _ensure_inbox_sections()
    print(f"[jiribilla] Done. Tenant id={tenant.id}, slug={TENANT_SLUG}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap the Jiribilla tenant.")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
