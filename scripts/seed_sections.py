# scripts/seed_sections.py
# Seed idempotente de ejemplo: crea Section "LandingPages", SectionSchema v1 y un Entry demo
from __future__ import annotations
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.services.content_service import create_section, add_schema_version, create_entry
from app.schemas.content import EntryCreate

TENANT_ID = 3  # ajusta si lo necesitas

LANDING_SCHEMA_V1 = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "LandingPage@1",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hero": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "subtitle": {"type": "string", "maxLength": 200},
                "background_image": {"type": "string", "format": "uri"},
                "cta": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string", "minLength": 1, "maxLength": 40},
                        "url": {"type": "string", "format": "uri"},
                    },
                    "required": ["label", "url"]
                }
            },
            "required": ["title"]
        },
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "icon": {"type": "string"},
                    "title": {"type": "string", "minLength": 1, "maxLength": 60},
                    "text": {"type": "string", "maxLength": 200}
                },
                "required": ["title", "text"]
            }
        },
        "seo": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "maxLength": 60},
                "description": {"type": "string", "maxLength": 160}
            }
        }
    },
    "required": ["hero"]
}


def run():
    db: Session = SessionLocal()
    try:
        # 1) Section
        section = create_section(
            db,
            tenant_id=TENANT_ID,
            key="LandingPages",
            name="Landing Pages",
            description="Páginas de aterrizaje del sitio"
        )

        # 2) Schema v1
        ss = add_schema_version(
            db,
            tenant_id=TENANT_ID,
            section_id=section.id,
            version=1,
            title="LandingPages schema v1",
            schema=LANDING_SCHEMA_V1
        )

        # 3) Entry demo (idempotente por slug)
        payload = EntryCreate(
            tenant_id=TENANT_ID,
            section_id=section.id,
            slug="home",
            schema_version=1,
            status="draft",
            data={
                "hero": {
                    "title": "Bienvenido a Latente CMS Core",
                    "subtitle": "Contenido flexible y versionado",
                    "background_image": "https://example.com/hero.jpg",
                    "cta": {"label": "Conoce más", "url": "https://zero2hero.lat"}
                },
                "features": [
                    {"icon": "bolt", "title": "Rápido", "text": "CRUD y publicación ligeros"},
                    {"icon": "layers", "title": "Flexible", "text": "JSONB + JSON Schema"},
                ],
                "seo": {"title": "Home", "description": "Landing del CMS Latente"}
            }
        )

        # Insertar si no existe la combinación única (tenant, section, slug)
        from sqlalchemy import select, and_
        from app.models.content import Entry
        exists = db.scalar(
            select(Entry).where(
                and_(Entry.tenant_id == TENANT_ID, Entry.section_id == section.id, Entry.slug == "home")
            )
        )
        if not exists:
            entry = create_entry(db, payload)
            db.commit()
            print(f"Seeded Entry id={entry.id}")
        else:
            db.commit()
            print("Entry 'home' ya existe; seed idempotente.")

    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
