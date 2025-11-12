from __future__ import annotations

"""
Bootstrap production/staging safely (no DROP), idempotent:

1) Alembic upgrade head
2) Seed core auth (roles/permissions + superadmins)
3) Ensure tenants (OWA, ANRO)
4) Load JSON Schemas from app/schemas/<tenant>/<section>/vX.json (activates highest per section)
5) Seed default content if files exist (OWA: home; ANRO: home/about/legacy_court/portfolio)
6) Ensure default editors linked to each tenant

Run on Heroku:
    heroku run --app <your-app> python -m scripts.bootstrap_prod
"""

import sys
from pathlib import Path
from typing import Optional
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

# Ensure repo root on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from scripts.seed_core_auth import run as seed_core_auth
from scripts.create_tenant import get_or_create_tenant
from scripts.add_tenant_member import run as add_member
from scripts.seed_tenant_schemas import run as seed_tenant_schemas
from scripts.seed_tenant_content import run as seed_tenant_content


def _alembic_upgrade_head() -> None:
    ini_path = (ROOT / "alembic.ini").as_posix()
    cfg = Config(ini_path)
    command.upgrade(cfg, "head")
    print("🔼 Alembic upgrade head OK")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _ensure_tenant(slug: str, name: Optional[str] = None):
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
    p = ROOT / content_path
    if not p.exists():
        print(f"⚠️  Content not found, skipping: {content_path}")
        return
    seed_tenant_content(
        tenant_key_or_name=tenant,
        section_key=section_key,
        slug=slug,
        content_path=p.as_posix(),
        schema_version_cli=schema_version,
        publish=publish,
        replace=False,
    )


def main() -> None:
    # 1) migrations
    _alembic_upgrade_head()

    # 2) core auth
    seed_core_auth()

    # 3) tenants
    _ensure_tenant("owa", "OWA")
    _ensure_tenant("anro", "ANRO")

    # 4) schemas (activates highest version per section)
    seed_tenant_schemas(tenant_key_or_name="owa", base_dir="app/schemas", set_active=[], dry_run=False)
    seed_tenant_schemas(tenant_key_or_name="anro", base_dir="app/schemas", set_active=[], dry_run=False)

    # 5) content (publish if files exist)
    _maybe_seed_content("owa",  "landing_pages", "home",         "content/owa/home_v1.json",              publish=True)
    _maybe_seed_content("anro", "home",          "home",         "content/anro/home_v1.json",             publish=True)
    _maybe_seed_content("anro", "about",         "about",        "content/anro/about_v1.json",            publish=True)
    _maybe_seed_content("anro", "legacy_court",  "legacy-court", "content/anro/legacy_court_v1.json",     publish=True)
    _maybe_seed_content("anro", "portfolio",     "portfolio",    "content/anro/portfolio_v1.json",        publish=True)

    # 6) default editors
    add_member(email="hello@owawellness.com", password="owa_password",  full_name="OWA Editor",  tenant_slug="owa",  role_key="editor")
    add_member(email="studio@anro.com",       password="anro_password", full_name="ANRO Editor", tenant_slug="anro", role_key="editor")

    print("\n✅ Bootstrap complete (no reset). Delivery samples:")
    print("  /delivery/v1/tenants/owa/sections/landing_pages/entries/home")
    print("  /delivery/v1/tenants/anro/sections/home/entries/home")
    print("  /delivery/v1/tenants/anro/sections/about/entries/about")
    print("  /delivery/v1/tenants/anro/sections/legacy_court/entries/legacy-court")
    print("  /delivery/v1/tenants/anro/sections/portfolio/entries/portfolio")


if __name__ == "__main__":
    main()
