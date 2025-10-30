# scripts/seed_auth.py
from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import select
from passlib.context import CryptContext

from app.db.session import SessionLocal
from app.models.auth import (
    User, Tenant, Role, Permission, RolePermission, UserTenant,
    RoleScope, PermissionScope, UserTenantStatus
)

pwd = CryptContext(
    schemes=["bcrypt"],
    bcrypt__truncate_error=False,  # ← evita la excepción
    deprecated="auto"
)

CORE_PERMS = [
    ("cms.sections.read", "Leer secciones", PermissionScope.core),
    ("cms.sections.write", "Escribir secciones", PermissionScope.core),
    ("cms.sections.publish", "Publicar secciones", PermissionScope.core),
    ("cms.sections.delete", "Eliminar secciones", PermissionScope.core),
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

ROLE_PERMS_MAP = {
    "tenant_admin": [p[0] for p in CORE_PERMS],
    "editor": [
        "cms.sections.read", "cms.sections.write", "cms.sections.publish",
        "cms.schemas.read"
    ],
    "author": ["cms.sections.read", "cms.sections.write", "cms.schemas.read"],
    "reviewer": ["cms.sections.read", "cms.sections.publish"],
    "viewer": ["cms.sections.read"],
    "api_client": ["cms.sections.read"]
}

SUPERADMIN_EMAIL = "admin@latente.local"
SUPERADMIN_PASS = "password"
TENANT_SLUG = "latente"
TENANT_NAME = "Latente Example"

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
        tenant, _ = get_or_create(db, Tenant, slug=TENANT_SLUG, defaults={"name": TENANT_NAME})
        perm_objs = {}
        for key, desc, scope in CORE_PERMS:
            perm, _ = get_or_create(db, Permission, key=key, defaults={"description": desc, "scope": scope})
            perm_objs[key] = perm

        role_objs = {}
        for key, label, scope, is_system in ROLES:
            role, _ = get_or_create(db, Role, key=key, defaults={"label": label, "scope": scope, "is_system": is_system})
            role_objs[key] = role

        for rkey, perm_keys in ROLE_PERMS_MAP.items():
            role = role_objs[rkey]
            for pkey in perm_keys:
                get_or_create(db, RolePermission, role_id=role.id, permission_id=perm_objs[pkey].id)

        superuser, _ = get_or_create(db, User, email=SUPERADMIN_EMAIL, defaults={
            "hashed_password": pwd.hash(SUPERADMIN_PASS),
            "full_name": "Super Admin",
            "is_active": True,
            "is_superadmin": True,
        })

        admin_user, _ = get_or_create(db, User, email="tenant_admin@latente.local", defaults={
            "hashed_password": pwd.hash("change_me"),
            "full_name": "Tenant Admin",
        })
        get_or_create(db, UserTenant, user_id=admin_user.id, tenant_id=tenant.id, defaults={
            "role_id": role_objs["tenant_admin"].id, "status": "active"
        })

        author, _ = get_or_create(db, User, email="author@latente.local", defaults={
            "hashed_password": pwd.hash("change_me"), "full_name": "Author Demo"
        })
        editor, _ = get_or_create(db, User, email="editor@latente.local", defaults={
            "hashed_password": pwd.hash("change_me"), "full_name": "Editor Demo"
        })
        get_or_create(db, UserTenant, user_id=author.id, tenant_id=tenant.id, defaults={
            "role_id": role_objs["author"].id, "status": UserTenantStatus.active
        })
        get_or_create(db, UserTenant, user_id=editor.id, tenant_id=tenant.id, defaults={
            "role_id": role_objs["editor"].id, "status": UserTenantStatus.active
        })

        db.commit()
        print("✅ Seeds aplicados correctamente")
    except Exception as e:
        db.rollback()
        print("❌ Error en seeds:", e)
    finally:
        db.close()

if __name__ == "__main__":
    main()
