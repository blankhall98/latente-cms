# scripts/add_owa_editor.py
from __future__ import annotations

import sys
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import User, Tenant, Role, UserTenant, UserTenantStatus
from app.services.passwords import hash_password

def now() -> datetime:
    return datetime.now(timezone.utc)

def run(email: str = "hello@owawellness.com", password: str = "owa123", tenant_slug: str = "owa") -> None:
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if not tenant:
            raise RuntimeError(f"Tenant '{tenant_slug}' no existe.")
        role_editor = db.scalar(select(Role).where(Role.key == "editor"))
        if not role_editor:
            raise RuntimeError("Rol 'editor' no existe. ¿Corriste seed_core_auth?")

        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(
                email=email,
                full_name="OWA Editor",
                hashed_password=hash_password(password),
                is_active=True,
                is_superadmin=False,
                created_at=now(),
                updated_at=now(),
            )
            db.add(user)
            db.flush()
            print(f"➕ Usuario creado: {email}")

        ut = db.scalar(
            select(UserTenant).where(
                UserTenant.user_id == user.id, UserTenant.tenant_id == tenant.id
            )
        )
        if not ut:
            ut = UserTenant(
                user_id=user.id,
                tenant_id=tenant.id,
                role_id=role_editor.id,
                status=UserTenantStatus.active,
            )
            db.add(ut)
            db.flush()
            print(f"🔗 Vinculado {email} → tenant '{tenant.slug}' como EDITOR")

        db.commit()
        print("✅ Listo: editor OWA creado y vinculado.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    # Optional CLI: python -m scripts.add_owa_editor [email] [password] [tenant_slug]
    email = sys.argv[1] if len(sys.argv) >= 2 else "hello@owawellness.com"
    password = sys.argv[2] if len(sys.argv) >= 3 else "owa123"
    tenant_slug = sys.argv[3] if len(sys.argv) >= 4 else "owa"
    run(email=email, password=password, tenant_slug=tenant_slug)

