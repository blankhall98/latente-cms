"""
Create (or reuse) a user and link them to a tenant with the given role.
Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is importable when called via `python -m scripts.add_tenant_member ...`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import User, Tenant, Role, UserTenant, UserTenantStatus
from app.services.passwords import hash_password


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(
    email: str,
    password: str,
    full_name: str,
    tenant_slug: str,
    role_key: str = "editor",
) -> None:
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if not tenant:
            raise RuntimeError(f"Tenant '{tenant_slug}' does not exist.")

        role = db.scalar(select(Role).where(Role.key == role_key))
        if not role:
            raise RuntimeError(f"Role '{role_key}' does not exist. Did you run seed_core_auth?")

        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(
                email=email,
                full_name=full_name,
                hashed_password=hash_password(password),
                is_active=True,
                is_superadmin=False,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(user)
            db.flush()
            print(f"[add-member] User created: {email}")
        else:
            # Ensure the user is active and has a password
            if not user.hashed_password:
                user.hashed_password = hash_password(password)
            if not user.is_active:
                user.is_active = True

        ut = db.scalar(
            select(UserTenant).where(
                UserTenant.user_id == user.id,
                UserTenant.tenant_id == tenant.id,
            )
        )
        if not ut:
            ut = UserTenant(
                user_id=user.id,
                tenant_id=tenant.id,
                role_id=role.id,
                status=UserTenantStatus.active,
            )
            db.add(ut)
            db.flush()
            print(f"[add-member] Linked {email} to '{tenant.slug}' as {role_key.upper()}")

        db.commit()
        print("[add-member] Tenant member OK.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    # Usage:
    #   python -m scripts.add_tenant_member <email> <password> "Full Name" <tenant_slug> [role_key]
    if len(sys.argv) < 5:
        print(
            "Usage: python -m scripts.add_tenant_member <email> <password> \"Full Name\" <tenant_slug> [role_key]"
        )
        sys.exit(2)

    email = sys.argv[1]
    password = sys.argv[2]
    full_name = sys.argv[3]
    tenant_slug = sys.argv[4]
    role_key = sys.argv[5] if len(sys.argv) >= 6 else "editor"

    run(email, password, full_name, tenant_slug, role_key)
