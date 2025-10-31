# scripts/seed_demo_client_owa.py
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import (
    User, Tenant, Role, UserTenant, RolePermission, UserTenantStatus
)
from app.models.content import Section, SectionSchema, Entry
from app.services.passwords import hash_password

SCHEMA_PATH = Path("app/schemas/owa/landing_pages/v2.json")
CONTENT_PATH = Path("content/owa/home_v2.json")

def now(): return datetime.now(timezone.utc)

def get_or_create(db: Session, model, where: dict, create: dict | None = None):
    obj = db.scalar(select(model).filter_by(**where))
    if obj:
        return obj, False
    payload = {**where, **(create or {})}
    obj = model(**payload)
    db.add(obj)
    db.flush()
    return obj, True

def run():
    db: Session = SessionLocal()
    try:
        # --- Tenant OWA ---
        owa, _ = get_or_create(db, Tenant, {"slug": "owa"}, {"name": "OWA", "is_active": True})

        # --- Usuario editor OWA ---
        ou, created = get_or_create(
            db, User, {"email": "hello@owawellness.com"},
            {
                "full_name": "OWA Editor",
                "hashed_password": hash_password("owa123"),
                "is_superadmin": False,
                "is_active": True,
                "created_at": now(), "updated_at": now(),
            }
        )
        if not created and not ou.hashed_password:
            ou.hashed_password = hash_password("owa123")

        # --- Rol Editor ---
        role_editor = db.scalar(select(Role).where(Role.key == "editor"))

        # --- Vincular OU ↔ OWA ---
        get_or_create(
            db, UserTenant,
            {"user_id": ou.id, "tenant_id": owa.id},
            {"role_id": role_editor.id, "status": UserTenantStatus.active},
        )

        # --- Sección + Schema v2 activo ---
        section, _ = get_or_create(
            db, Section,
            {"tenant_id": owa.id, "key": "LandingPages"},
            {"name": "Landing Pages", "description": "OWA landing"}
        )

        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(SCHEMA_PATH.as_posix())
        schema_v2 = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        ss, _ = get_or_create(
            db, SectionSchema,
            {"tenant_id": owa.id, "section_id": section.id, "version": 2},
            {"title": "OWA Landing v2", "schema": schema_v2, "is_active": True}
        )
        ss.is_active = True

        # --- Contenido 'home' v2 publicado ---
        if not CONTENT_PATH.exists():
            raise FileNotFoundError(CONTENT_PATH.as_posix())
        content_payload = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))

        entry, _ = get_or_create(
            db, Entry,
            {"tenant_id": owa.id, "section_id": section.id, "slug": "home"},
            {"schema_version": 2, "status": "draft", "data": content_payload, "created_at": now(), "updated_at": now()}
        )
        entry.status = "published"
        entry.published_at = now()
        entry.archived_at = None
        entry.updated_at = now()

        db.commit()
        print("[OK] Demo OWA sembrado: usuario, tenant, sección, schema v2 y 'home' publicado.")
        print("Login OWA: hello@owawellness.com / owa123")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run()
