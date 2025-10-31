# scripts/seed_core_auth.py
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import User, Role, Permission, RolePermission
from app.services.passwords import hash_password

def now(): return datetime.now(timezone.utc)

def get_or_create(db: Session, model, where: dict, create: dict | None = None):
    obj = db.scalar(select(model).filter_by(**where))
    if obj:
        return obj, False
    obj = model(**{**where, **(create or {})})
    db.add(obj)
    db.flush()
    return obj, True

def run():
    db: Session = SessionLocal()
    try:
        # Roles
        role_keys = [
            ("super_admin", "Super Admin"),
            ("tenant_admin", "Tenant Admin"),
            ("editor", "Editor"),
            ("author", "Author"),
            ("viewer", "Viewer"),
        ]
        role_map = {}
        for key, label in role_keys:
            r, _ = get_or_create(db, Role, {"key": key}, {"label": label, "is_system": True})
            role_map[key] = r

        # Permisos
        perm_keys = [
            ("content:read", "Leer"),
            ("content:write", "Escribir"),
            ("content:publish", "Publicar"),
        ]
        perm_map = {}
        for key, desc in perm_keys:
            p, _ = get_or_create(db, Permission, {"key": key}, {"description": desc})
            perm_map[key] = p

        # Matriz rol → permisos
        matrix = {
            "viewer": ["content:read"],
            "author": ["content:read", "content:write"],
            "editor": ["content:read", "content:write", "content:publish"],
            "tenant_admin": ["content:read", "content:write", "content:publish"],
            "super_admin": ["content:read", "content:write", "content:publish"],
        }
        for rk, pks in matrix.items():
            for pk in pks:
                get_or_create(db, RolePermission, {"role_id": role_map[rk].id, "permission_id": perm_map[pk].id}, {})

        # Superadmins (con hash real)
        for email, name, pwd in [
            ("zero2hero@demo.com", "Zero2Hero Admin", "admin123"),
            ("latente@demo.com", "Latente Admin", "admin123"),
        ]:
            u, created = get_or_create(
                db,
                User,
                {"email": email},
                {
                    "full_name": name,
                    "hashed_password": hash_password(pwd),
                    "is_superadmin": True,
                    "is_active": True,
                    "created_at": now(),
                    "updated_at": now(),
                },
            )
            if not created and not u.hashed_password:
                u.hashed_password = hash_password(pwd)

        db.commit()
        print("[OK] Roles, permisos y superadmins sembrados.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run()
