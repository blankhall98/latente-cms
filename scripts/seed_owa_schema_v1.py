# scripts/seed_owa_schema_v1.py
from __future__ import annotations
import json
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Section, SectionSchema

SCHEMA_PATH = Path("app/schemas/owa/landing_pages/v1.json")

def _get_or_create(db: Session, model, where: dict, create: dict | None = None):
    obj = db.scalar(select(model).filter_by(**where))
    if obj:
        return obj, False
    obj = model(**{**where, **(create or {})})
    db.add(obj)
    db.flush()
    return obj, True

def run(tenant_slug: str = "owa"):
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if not tenant:
            raise RuntimeError(f"Tenant '{tenant_slug}' no existe. Crea primero el tenant.")

        section, _ = _get_or_create(
            db, Section,
            {"tenant_id": tenant.id, "key": "LandingPages"},
            {"name": "Landing Pages", "description": "Página de inicio OWA (content-only v1)"}
        )

        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(SCHEMA_PATH.as_posix())
        schema_v1 = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        ss, _ = _get_or_create(
            db, SectionSchema,
            {"tenant_id": tenant.id, "section_id": section.id, "version": 1},
            {"title": "OWA Landing v1 (content-only)", "schema": schema_v1, "is_active": True}
        )
        ss.is_active = True  # asegurar activo
        db.commit()
        print(f"✅ Schema v1 activo para tenant='{tenant_slug}' sección='LandingPages'")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run()
