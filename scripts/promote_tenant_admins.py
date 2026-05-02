"""Promote primary client accounts to tenant_admin role."""
from app.db.session import SessionLocal
from app.models.auth import User, UserTenant, Role, Tenant
from sqlalchemy import select

PROMOTIONS = [
    ("studio@anro.com",        "anro"),
    ("dewa@cms.com",           "dewa"),
    ("hello@owawellness.com",  "owa"),
]

db = SessionLocal()
tenant_admin = db.scalar(select(Role).where(Role.key == "tenant_admin"))
if not tenant_admin:
    print("ERROR: tenant_admin role not found")
    db.close()
    exit(1)

for email, slug in PROMOTIONS:
    user = db.scalar(select(User).where(User.email == email))
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not user:
        print(f"SKIP {email}: user not found")
        continue
    if not tenant:
        print(f"SKIP {slug}: tenant not found")
        continue
    ut = db.scalar(
        select(UserTenant).where(
            UserTenant.user_id == user.id,
            UserTenant.tenant_id == tenant.id,
        )
    )
    if not ut:
        print(f"SKIP {email} / {slug}: no UserTenant record")
        continue
    ut.role_id = tenant_admin.id
    print(f"Promoted {email} on {slug} to tenant_admin")

db.commit()
print("Done.")
db.close()
