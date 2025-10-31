# scripts/bootstrap_minimal_owa.py
# v2025-10-31-03  ✅ usa hashed_password al crear usuarios
#                  ✅ UserTenant usa status=active (no is_active)
#                  ✅ idempotente: re-activa schema y publica 'home'

from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import (
    User, Tenant, Role, Permission, UserTenant, RolePermission, UserTenantStatus
)
from app.models.content import Section, SectionSchema, Entry
from app.services.passwords import hash_password

SCHEMA_PATH = Path("app/schemas/owa/landing_pages/v2.json")
CONTENT_PATH = Path("content/owa/home_v2.json")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create(db: Session, model, where: dict, create: dict | None = None):
    obj = db.scalar(select(model).filter_by(**where))
    if obj:
        return obj, False
    payload = {**where, **(create or {})}
    obj = model(**payload)
    db.add(obj)
    db.flush()  # asegura PK disponible
    return obj, True


def ensure_roles_perms(db: Session):
    roles = [
        ("super_admin", "Super Admin"),
        ("tenant_admin", "Tenant Admin"),
        ("editor", "Editor"),
        ("author", "Author"),
        ("viewer", "Viewer"),
    ]
    perms = [
        ("content:read", "Leer contenido"),
        ("content:write", "Escribir contenido"),
        ("content:publish", "Publicar contenido"),
    ]

    role_map = {}
    for key, label in roles:
        r, _ = get_or_create(db, Role, {"key": key}, {"label": label, "is_system": True})
        role_map[key] = r

    perm_map = {}
    for key, desc in perms:
        p, _ = get_or_create(db, Permission, {"key": key}, {"description": desc})
        perm_map[key] = p

    matrix = {
        "viewer": ["content:read"],
        "author": ["content:write", "content:read"],
        "editor": ["content:write", "content:read", "content:publish"],
        "tenant_admin": ["content:write", "content:read", "content:publish"],
        "super_admin": ["content:write", "content:read", "content:publish"],
    }
    for role_key, perm_keys in matrix.items():
        r = role_map[role_key]
        for pk in perm_keys:
            p = perm_map[pk]
            get_or_create(db, RolePermission, {"role_id": r.id, "permission_id": p.id}, {})

    return role_map


def ensure_users_tenant(db: Session, role_map):
    print("[bootstrap] ensure_users_tenant() — versión v2025-10-31-03")

    # Superadmins (SIEMPRE con hashed_password en create_kwargs)
    sa1, created1 = get_or_create(
        db,
        User,
        {"email": "zero2hero@demo.com"},
        {
            "full_name": "Zero2Hero Admin",
            "hashed_password": hash_password("admin123"),
            "is_superadmin": True,
            "is_active": True,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        },
    )
    if not created1 and not sa1.hashed_password:
        sa1.hashed_password = hash_password("admin123")

    sa2, created2 = get_or_create(
        db,
        User,
        {"email": "latente@demo.com"},
        {
            "full_name": "Latente Admin",
            "hashed_password": hash_password("admin123"),
            "is_superadmin": True,
            "is_active": True,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        },
    )
    if not created2 and not sa2.hashed_password:
        sa2.hashed_password = hash_password("admin123")

    # Tenant OWA
    owa, _ = get_or_create(db, Tenant, {"slug": "owa"}, {"name": "OWA", "is_active": True})

    # Usuario OWA (editor)
    ou, created_ou = get_or_create(
        db,
        User,
        {"email": "hello@owawellness.com"},
        {
            "full_name": "OWA Editor",
            "hashed_password": hash_password("owa123"),
            "is_superadmin": False,
            "is_active": True,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        },
    )
    if not created_ou and not ou.hashed_password:
        ou.hashed_password = hash_password("owa123")

    # Vínculo OU ↔ OWA con rol Editor (usar status, NO is_active)
    get_or_create(
        db,
        UserTenant,
        {"user_id": ou.id, "tenant_id": owa.id},
        {"role_id": role_map["editor"].id, "status": UserTenantStatus.active},
    )

    return owa


def ensure_owa_content(db: Session, owa: Tenant):
    section, _ = get_or_create(
        db,
        Section,
        {"tenant_id": owa.id, "key": "LandingPages"},
        {"name": "Landing Pages", "description": "OWA landing components"},
    )

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(SCHEMA_PATH.as_posix())
    schema_v2 = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    ss, _ = get_or_create(
        db,
        SectionSchema,
        {"tenant_id": owa.id, "section_id": section.id, "version": 2},
        {"title": "OWA Landing v2", "schema": schema_v2, "is_active": True},
    )
    ss.is_active = True  # reactivar si ya existía

    if not CONTENT_PATH.exists():
        raise FileNotFoundError(CONTENT_PATH.as_posix())
    data = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))

    entry, _ = get_or_create(
        db,
        Entry,
        {"tenant_id": owa.id, "section_id": section.id, "slug": "home"},
        {
            "schema_version": 2,
            "status": "draft",
            "data": data,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        },
    )
    # Publicar/republish idempotente
    entry.status = "published"
    entry.published_at = now_utc()
    entry.archived_at = None
    entry.updated_at = now_utc()


def run():
    print("[bootstrap] scripts/bootstrap_minimal_owa.py v2025-10-31-03")
    db: Session = SessionLocal()
    try:
        role_map = ensure_roles_perms(db)
        owa = ensure_users_tenant(db, role_map)
        ensure_owa_content(db, owa)
        db.commit()
        print("[OK] Bootstrap mínimo completado: superadmins, OWA + usuario editor y Home publicado.")
        print("Login superadmin: zero2hero@demo.com / admin123")
        print("Login superadmin: latente@demo.com / admin123")
        print("Login OWA: hello@owawellness.com / owa123")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()

