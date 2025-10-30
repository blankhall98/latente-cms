# scripts/seed_auth_minimal.py
from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import select
from passlib.context import CryptContext

from app.db.session import SessionLocal
from app.models.auth import (
    User, Role, Permission, RolePermission,
    RoleScope, PermissionScope
)

pwd = CryptContext(schemes=["bcrypt"], bcrypt__truncate_error=False, deprecated="auto")

CORE_PERMS = [
    ("cms.sections.read", "Leer secciones", PermissionScope.core),
    ("cms.sections.write", "Escribir secciones", PermissionScope.core),
    ("cms.sections.publish", "Publicar secciones", PermissionScope.core),
    ("cms.schemas.read", "Leer schemas", PermissionScope.core),
    ("cms.schemas.write", "Escribir schemas", PermissionScope.core),
    ("cms.publish.trigger", "Disparar publicación", PermissionScope.core),
    ("cms.cache.purge", "Purgar caché", PermissionScope.core),
    ("cms.tenants.manage", "Administrar tenants", PermissionScope.core),
    ("cms.roles.manage", "Administrar roles", PermissionScope.core),
    ("cms.users.manage", "Administrar usuarios", PermissionScope.core),
]

ROLES = [
    ("super_admin", "Super Admin", RoleScope.core, True),
    ("tenant_admin", "Administrador de Tenant", RoleScope.core, True),
    ("editor", "Editor", RoleScope.core, True),
    ("author", "Autor", RoleScope.core, True),
    ("reviewer", "Revisor", RoleScope.core, True),
    ("viewer", "Lector", RoleScope.core, True),
    ("api_client", "Cliente API", RoleScope.core, True),
]

def get_or_create(session: Session, model, defaults=None, **kwargs):
    stmt = select(model).filter_by(**kwargs)
    instance = session.execute(stmt).scalar_one_or_none()
    if instance:
        return instance, False
    params = {**kwargs, **(defaults or {})}
    instance = model(**params)
    session.add(instance)
    session.flush()
    return instance, True

def main():
    db: Session = SessionLocal()
    try:
        perm_objs = {}
        for key, desc, scope in CORE_PERMS:
            perm, _ = get_or_create(db, Permission, key=key, defaults={"description": desc, "scope": scope})
            perm_objs[key] = perm

        role_objs = {}
        for key, label, scope, is_system in ROLES:
            role, _ = get_or_create(db, Role, key=key, defaults={"label": label, "scope": scope, "is_system": is_system})
            role_objs[key] = role

        # example: dar a tenant_admin todos los permisos core
        for p in perm_objs.values():
            get_or_create(db, RolePermission, role_id=role_objs["tenant_admin"].id, permission_id=p.id)

        # Superadmins globales
        for email, name, raw in [
            ("xblankhallx@gmail.com", "Zero2Hero", "password"),
            ("rodrigo@latentestudio.com",   "Latente",   "password"),
        ]:
            get_or_create(
                db, User, email=email,
                defaults={
                    "hashed_password": pwd.hash(raw),
                    "full_name": name,
                    "is_active": True,
                    "is_superadmin": True,
                }
            )

        db.commit()
        print("✅ Seed auth mínimo OK (roles/permisos + 2 superadmins)")
    except Exception as e:
        db.rollback()
        print("❌ Error en seeds:", e)
        raise
    finally:
        db.close()

if __name__ == "__main__":
    main()
