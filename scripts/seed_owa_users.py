# scripts/seed_owa_users.py
from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import select
from passlib.context import CryptContext

from app.db.session import SessionLocal
from app.models.auth import (
    User, Tenant, Role, UserTenant, UserTenantStatus
)

pwd = CryptContext(schemes=["bcrypt"], bcrypt__truncate_error=False, deprecated="auto")

def _get(db: Session, model, **by):
    return db.execute(select(model).filter_by(**by)).scalar_one_or_none()

def run():
    db: Session = SessionLocal()
    try:
        tenant = _get(db, Tenant, slug="owa")
        if not tenant:
            # crea tenant OWA (idempotente si ya existe)
            from scripts.create_tenant import get_or_create_tenant
            tenant = get_or_create_tenant(db, name="OWA", slug="owa")
            db.commit()
            print(f"[OK] Tenant OWA id={tenant.id}")

        role_editor  = _get(db, Role, key="editor")
        role_author  = _get(db, Role, key="author")
        role_viewer  = _get(db, Role, key="viewer")
        role_admin   = _get(db, Role, key="tenant_admin")

        # Usuarios OWA (no superadmins)
        users = [
            ("admin@owa.local",  "OWA Admin",  "password", role_admin),
            ("editor@owa.local", "OWA Editor", "password", role_editor),
            ("author@owa.local", "OWA Author", "password", role_author),
            ("viewer@owa.local", "OWA Viewer", "password", role_viewer),
        ]

        for email, name, raw, role in users:
            u = _get(db, User, email=email)
            if not u:
                u = User(email=email, full_name=name, hashed_password=pwd.hash(raw), is_active=True, is_superadmin=False)
                db.add(u)
                db.flush()
            ut = _get(db, UserTenant, user_id=u.id, tenant_id=tenant.id)
            if not ut:
                ut = UserTenant(user_id=u.id, tenant_id=tenant.id, role_id=role.id, status=UserTenantStatus.active)
                db.add(ut)
        db.commit()
        print("✅ OWA users + memberships OK")
    finally:
        db.close()

if __name__ == "__main__":
    run()
