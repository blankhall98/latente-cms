# scripts/bootstrap_prod.py
from __future__ import annotations

"""
Bootstrap production/staging safely (no DROP), idempotent:

1) Alembic upgrade head
2) Seed core auth (roles/permissions + superadmins)
3) Ensure tenants (OWA, ANRO)
4) Load JSON Schemas from app/schemas/<tenant>/<section>/vX.json (activates highest per section)
5) Seed default content if files exist (no error if missing)
6) Ensure default editors linked to each tenant
"""

import os
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config

from app.db.session import SessionLocal
from sqlalchemy.orm import Session

from scripts.seed_core_auth import run as seed_core_auth
from scripts.create_tenant import get_or_create_tenant
from scripts.add_tenant_member import run as add_member
from scripts.seed_tenant_schemas import run as seed_tenant_schemas
from scripts.seed_tenant_content import run as seed_tenant_content

ROOT = Path(__file__).resolve().parents[1]

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
        content_path=str(p.as_posix()),
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

    # 5) content (only if files exist)
    _maybe_seed_content(
        tenant="owa",
        section_key="landing_pages",   # must match your section key created by schema loader
        slug="home",
        content_path="content/owa/home_v1.json",
        schema_version=None,
        publish=True,
    )
    _maybe_seed_content(
        tenant="anro",
        section_key="home",            # must match the ANRO section key
        slug="home",
        content_path="content/anro/home_v1.json",
        schema_version=None,
        publish=True,
    )

    # 6) default editors
    add_member(
        email="hello@owawellness.com",
        password="owa_password",
        full_name="OWA Editor",
        tenant_slug="owa",
        role_key="editor",
    )
    add_member(
        email="studio@anro.com",
        password="anro_password",
        full_name="ANRO Editor",
        tenant_slug="anro",
        role_key="editor",
    )

    print("\n✅ Bootstrap complete (no reset).")

if __name__ == "__main__":
    main()
