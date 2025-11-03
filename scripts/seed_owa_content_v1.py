# scripts/seed_owa_content_v1.py
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Section, Entry

CONTENT_PATH = Path("content/owa/home_v1.json")

def now_utc():
    return datetime.now(timezone.utc)

def run(tenant_slug: str = "owa"):
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if not tenant:
            raise RuntimeError(f"Tenant '{tenant_slug}' no existe. Crea primero el tenant.")

        section = db.scalar(
            select(Section).where(and_(Section.tenant_id == tenant.id, Section.key == "LandingPages"))
        )
        if not section:
            raise RuntimeError("Section 'LandingPages' no existe. Corre primero seed_owa_schema_v1.py")

        if not CONTENT_PATH.exists():
            raise FileNotFoundError(CONTENT_PATH.as_posix())
        payload = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))

        entry = db.scalar(
            select(Entry).where(
                and_(Entry.tenant_id == tenant.id, Entry.section_id == section.id, Entry.slug == "home")
            )
        )
        if not entry:
            entry = Entry(
                tenant_id=tenant.id,
                section_id=section.id,
                slug="home",
                schema_version=1,
                status="draft",
                data=payload,
                created_at=now_utc(),
                updated_at=now_utc(),
            )
            db.add(entry)
            db.flush()
            print(f"➕ Creado entry 'home' (draft) id={entry.id}")
        else:
            entry.data = payload
            entry.schema_version = 1
            entry.updated_at = now_utc()
            print(f"♻️  Actualizado entry 'home' id={entry.id} a schema_version=1")

        # Publicar para que el delivery ya funcione
        entry.status = "published"
        entry.published_at = now_utc()
        entry.archived_at = None

        db.commit()
        print("✅ 'home' publicado en v1 para OWA.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run()
