"""One-time script: drop author roles, migrate users, verify final state."""
from app.db.session import SessionLocal
from app.models.auth import Role, UserTenant, RolePermission
from sqlalchemy import select, delete

db = SessionLocal()

editor = db.scalar(select(Role).where(Role.key == "editor"))
author = db.scalar(select(Role).where(Role.key == "author"))
orphan = db.scalar(select(Role).where(Role.key == "author_e11c803d"))

if author and editor:
    n = db.query(UserTenant).filter(UserTenant.role_id == author.id).update({"role_id": editor.id})
    print(f"Migrated {n} author user(s) to editor")
    db.execute(delete(RolePermission).where(RolePermission.role_id == author.id))
    db.delete(author)
    print("Deleted 'author' role")

if orphan:
    db.execute(delete(RolePermission).where(RolePermission.role_id == orphan.id))
    db.delete(orphan)
    print("Deleted 'author_e11c803d' orphan role")

db.commit()

print("\nFinal roles:")
for r in db.scalars(select(Role).order_by(Role.id)):
    print(f"  [{r.id}] {r.key} — {r.label}")

db.close()
