# scripts/reset_and_seed_all.py
from __future__ import annotations

"""
One-shot local bootstrap:

1) Drop & recreate the public schema
2) Run Alembic migrations to head (via Alembic API)
3) Seed core auth (roles/permissions + superadmins)
4) Create tenants: OWA and ANRO
5) Load JSON Schemas from app/schemas/<tenant>/<section>/vX.json (activates highest version per section)
6) Seed content for each tenant if content JSON exists
7) Create default tenant members (editors) for OWA and ANRO

Run:
    python -m scripts.reset_and_seed_all
"""

import sys
from pathlib import Path
from typing import Optional

# Ensure repo root on path so "app.*" imports work when run as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from scripts.seed_core_auth import run as seed_core_auth
from scripts.create_tenant import get_or_create_tenant
from scripts.add_tenant_member import run as add_member
from scripts.seed_tenant_content import run as seed_tenant_content
from scripts.seed_tenant_schemas import run as seed_tenant_schemas

# --- Alembic API (no subprocess; works on Windows)
from alembic import command
from alembic.config import Config


def reset_public_schema() -> None:
    """Hard reset of the 'public' schema — local/dev only."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE;"))
        conn.execute(text("CREATE SCHEMA public;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
    print("🧹 public schema dropped & recreated")


def alembic_upgrade_head() -> None:
    # Ensure we point to the repo's alembic.ini
    ini_path = (ROOT / "alembic.ini").as_posix()
    cfg = Config(ini_path)
    # If your alembic.ini doesn't set script_location, uncomment:
    # cfg.set_main_option("script_location", "migrations")
    command.upgrade(cfg, "head")
    print("🔼 Alembic upgrade head OK")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _ensure_tenant(slug: str, name: Optional[str] = None):
    """Idempotent create-or-get by slug (or name)."""
    db: Session = SessionLocal()
    try:
        t = get_or_create_tenant(db, name=(name or slug.upper()), slug=slug)
        _commit(db)
        print(f"🏷️  Tenant ensured: id={t.id} name={t.name} slug={t.slug}")
        return t
    finally:
        db.close()


def _maybe_seed_content(
    tenant: str,
    section_key: str,
    slug: str,
    content_path: str,
    schema_version: Optional[int] = None,
    publish: bool = True,
) -> None:
    if not Path(content_path).exists():
        print(f"⚠️  Content file not found, skipping: {content_path}")
        return
    seed_tenant_content(
        tenant_key_or_name=tenant,
        section_key=section_key,
        slug=slug,
        content_path=content_path,
        schema_version_cli=schema_version,
        publish=publish,
        replace=False,
    )


def main() -> None:
    # 1) Drop & recreate schema
    reset_public_schema()

    # 2) Apply migrations via Alembic API
    alembic_upgrade_head()

    # 3) Core auth (roles, permissions, superadmins)
    seed_core_auth()

    # 4) Tenants
    _ensure_tenant("owa", "OWA")
    _ensure_tenant("anro", "ANRO")

    # 5) Load JSON Schemas (activates highest version per section)
    seed_tenant_schemas(tenant_key_or_name="owa", base_dir="app/schemas", set_active=[], dry_run=False)
    seed_tenant_schemas(tenant_key_or_name="anro", base_dir="app/schemas", set_active=[], dry_run=False)

    # 6) Seed content
    _maybe_seed_content(
        tenant="owa",
        section_key="landing_pages",  # ✅ match section key created by schema loader
        slug="home",
        content_path="content/owa/home_v1.json",
        schema_version=None,
        publish=True,
    )
    _maybe_seed_content(
        tenant="anro",
        section_key="home",
        slug="home",
        content_path="content/anro/home_v1.json",
        schema_version=None,
        publish=True,
    )

    # 7) Default editors
    add_member(
        email="hello@owawellness.com",
        password="owa123",
        full_name="OWA Editor",
        tenant_slug="owa",
        role_key="editor",
    )
    add_member(
        email="studio@anro.com",
        password="anro123",
        full_name="ANRO Editor",
        tenant_slug="anro",
        role_key="editor",
    )

    print("\n✅ All done.")
    print("• Superadmins: zero2hero@demo.com / latente@demo.com (admin123)")
    print("• OWA editor:  hello@owawellness.com / owa123")
    print("• ANRO editor: studio@anro.com / anro123")
    print("\nDelivery examples (once content exists):")
    print("  /delivery/v1/tenants/owa/sections/landing_pages/entries/home")
    print("  /delivery/v1/tenants/anro/sections/home/entries/home")


if __name__ == "__main__":
    main()




