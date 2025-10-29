# scripts/create_tenant.py
# Crea un Tenant por nombre/slug para luego cargar sus schemas
from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.auth import Tenant  # asume que existe este modelo (ya lo tienes del Paso 7)

def get_or_create_tenant(db: Session, *, name: str, slug: str | None = None) -> Tenant:
    q = select(Tenant).where(Tenant.name == name)
    t = db.scalar(q)
    if t:
        return t
    t = Tenant(name=name, slug=slug or name.lower())
    db.add(t)
    db.flush()
    return t

def run(name: str, slug: str | None = None):
    db: Session = SessionLocal()
    try:
        t = get_or_create_tenant(db, name=name, slug=slug)
        db.commit()
        print(f"[OK] Tenant id={t.id} name={t.name} slug={t.slug}")
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.create_tenant <name> [slug]")
        raise SystemExit(1)
    name = sys.argv[1]
    slug = sys.argv[2] if len(sys.argv) >= 3 else None
    run(name, slug)
