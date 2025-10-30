# tests/test_owa_v2_seed.py
from __future__ import annotations
import json
from pathlib import Path
from sqlalchemy import select, and_
from jsonschema import Draft202012Validator

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Section, SectionSchema, Entry

SCHEMA_PATH = Path("app/schemas/owa/landing_pages/v2.json")

def test_owa_v2_schema_and_entry_exist_and_validate():
    assert SCHEMA_PATH.exists(), "Schema v2 no existe en ruta esperada"
    schema_v2 = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema_v2)  # schema es válido

    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "owa"))
        assert tenant is not None, "Tenant 'owa' no existe; ejecuta scripts.create_tenant OWA owa"

        section = db.scalar(
            select(Section).where(and_(Section.tenant_id == tenant.id, Section.key == "LandingPages"))
        )
        assert section is not None, "Sección LandingPages no existe; corre seed_owa_v2"

        ss_v2 = db.scalar(
            select(SectionSchema).where(
                and_(
                    SectionSchema.section_id == section.id,
                    SectionSchema.version == 2,
                    SectionSchema.is_active.is_(True),
                )
            )
        )
        assert ss_v2 is not None, "No hay SectionSchema v2 activo para LandingPages"

        entry = db.scalar(
            select(Entry).where(
                and_(Entry.tenant_id == tenant.id, Entry.section_id == section.id, Entry.slug == "home")
            )
        )
        assert entry is not None, "Entry 'home' no existe; corre seed_owa_v2"
        assert entry.schema_version == 2, "Entry 'home' no está en schema_version=2"

        # Validamos el data del entry contra el schema v2
        Draft202012Validator(schema_v2).validate(entry.data)
    finally:
        db.close()
