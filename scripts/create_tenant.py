# scripts/create_tenant.py
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# --- Ensure repo root is on sys.path so "app.*" imports work when run as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import Tenant


def get_or_create_tenant(db: Session, *, name: str, slug: Optional[str] = None) -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.name == name))
    if t:
        return t
    t = Tenant(name=name, slug=(slug or name.lower()))
    db.add(t)
    db.flush()
    return t


def run(name: str, slug: Optional[str] = None) -> None:
    db: Session = SessionLocal()
    try:
        t = get_or_create_tenant(db, name=name, slug=slug)
        db.commit()
        print(f"[OK] Tenant id={t.id} name={t.name} slug={t.slug}")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(
        description="Create (or get) a tenant by name/slug.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Primary interface (flags)
    ap.add_argument("--name", help="Tenant name (e.g., ANRO)")
    ap.add_argument("--slug", help="Tenant slug (e.g., anro)")

    # Back-compat: allow positional args: <name> [slug]
    ap.add_argument("positional_name", nargs="?", help="(positional) name")
    ap.add_argument("positional_slug", nargs="?", help="(positional) slug")

    args = ap.parse_args()

    name = args.name or args.positional_name
    slug = args.slug or args.positional_slug

    if not name:
        print("Usage:\n"
              "  python scripts/create_tenant.py --name ANRO --slug anro\n"
              "  # or (legacy)\n"
              "  python scripts/create_tenant.py ANRO anro")
        sys.exit(2)

    run(name=name, slug=slug)


if __name__ == "__main__":
    main()

