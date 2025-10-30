# scripts/seed_owa_v2.py
from __future__ import annotations
import json
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry
from app.schemas.content import EntryCreate, EntryUpdate
from app.services.content_service import create_section, add_schema_version, update_entry, create_entry

SCHEMA_PATH = Path("app/schemas/owa/landing_pages/v2.json")
CONTENT_PATH = Path("content/owa/home_v2.json")

def _get_tenant(db: Session, slug: str = "owa") -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not t:
        raise RuntimeError(f"Tenant slug='{slug}' no encontrado. Crea el tenant primero (scripts/create_tenant.py).")
    return t

def _validate_payload(schema: dict, payload: dict):
    try:
        from jsonschema import validate, Draft202012Validator
        Draft202012Validator.check_schema(schema)
        validate(instance=payload, schema=schema, cls=Draft202012Validator)
    except Exception as e:
        raise RuntimeError(f"❌ Payload no valida contra el schema v2: {e}") from e

def run():
    db: Session = SessionLocal()
    try:
        # --- Tenant OWA ---
        tenant = _get_tenant(db, slug="owa")

        # --- Sección + Schema v2 ---
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"No existe schema: {SCHEMA_PATH.as_posix()}")
        schema_v2 = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        section = create_section(
            db,
            tenant_id=tenant.id,
            key="LandingPages",
            name="Landing Pages",
            description="Página de inicio OWA (layout por secciones/componentes)"
        )
        db.flush()

        add_schema_version(
            db,
            tenant_id=tenant.id,
            section_id=section.id,
            version=2,
            title="OWA Landing v2 (components)",
            schema=schema_v2,
            is_active=True,  # dejamos v2 activo
        )

        # --- Contenido real (home_v2.json) ---
        if not CONTENT_PATH.exists():
            raise FileNotFoundError(f"No existe contenido: {CONTENT_PATH.as_posix()}")
        content_payload = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))

        # Validación estricta contra v2
        _validate_payload(schema_v2, content_payload)

        # --- Upsert del Entry 'home' con schema_version=2 ---
        existing = db.scalar(
            select(Entry).where(
                and_(Entry.tenant_id == tenant.id, Entry.section_id == section.id, Entry.slug == "home")
            )
        )

        if not existing:
            payload = EntryCreate(
                tenant_id=tenant.id,
                section_id=section.id,
                slug="home",
                schema_version=2,
                status="draft",  # lo dejamos draft, publicarás vía endpoint/flujo normal
                data=content_payload,
            )
            entry = create_entry(db, payload)
            print(f"[OK] Creado entry draft slug='home' id={entry.id}")
        else:
            # conservamos el status actual; solo sincronizamos data y schema_version
            payload = EntryUpdate(
                data=content_payload,
                schema_version=2,
            )
            entry = update_entry(db, existing.id, payload)
            print(f"[OK] Actualizado entry slug='home' id={entry.id} a schema_version=2")

        db.commit()
        print("[OK] OWA v2: schema activado + contenido real cargado")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run()



