from app.db.session import SessionLocal
from app.models.auth import User, UserTenant, Role, Tenant
from sqlalchemy import select
db = SessionLocal()
rows = db.execute(
    select(User.email, Role.key, Tenant.slug)
    .join(UserTenant, UserTenant.user_id == User.id)
    .join(Role, Role.id == UserTenant.role_id)
    .join(Tenant, Tenant.id == UserTenant.tenant_id)
    .order_by(Tenant.slug, User.email)
).all()
for email, role_key, slug in rows:
    print(f"{slug:12} {role_key:20} {email}")
db.close()
