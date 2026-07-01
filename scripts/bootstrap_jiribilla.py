from __future__ import annotations

"""
Bootstrap Jiribilla in an existing database.

Safe to run multiple times:
  - tenant is created or reused by slug/name
  - schemas are loaded from app/schemas/jiribilla
  - content entries are upserted and published
  - settings entry is created and published with hola@jiribilla.studio

Usage:
    python -m scripts.bootstrap_jiribilla
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.auth import Tenant  # noqa: E402
from app.models.content import Section  # noqa: E402
from scripts.bootstrap_tenant_settings import run as seed_tenant_settings  # noqa: E402
from scripts.seed_tenant_content import run as seed_tenant_content  # noqa: E402
from scripts.seed_tenant_schemas import run as seed_tenant_schemas  # noqa: E402


TENANT_NAME = "Jiribilla"
TENANT_SLUG = "jiribilla"
CONTACT_EMAIL = "hola@jiribilla.studio"

SECTIONS = [
    "hero",
    "mesa_uno",
    "proyectos",
    "eventos_privados",
    "glosario",
    "equipo",
    "footer",
    "social_links",
    "forms",
    "privacy_policy",
]

SECTION_LABELS = {
    "hero": "Hero",
    "mesa_uno": "Mesa Uno",
    "proyectos": "Proyectos",
    "eventos_privados": "Eventos Privados",
    "glosario": "Glosario",
    "equipo": "Equipo",
    "footer": "Footer",
    "social_links": "Social and Links",
    "forms": "Forms",
    "privacy_policy": "Privacy Policy",
}


def _ensure_tenant() -> Tenant:
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(
            select(Tenant).where(
                or_(Tenant.slug == TENANT_SLUG, Tenant.name == TENANT_NAME)
            )
        )
        if tenant is None:
            tenant = Tenant(name=TENANT_NAME, slug=TENANT_SLUG)
            db.add(tenant)
            db.flush()
            print(f"[jiribilla] Tenant created: id={tenant.id} slug={tenant.slug}")
        else:
            print(f"[jiribilla] Tenant reused: id={tenant.id} slug={tenant.slug}")

        if tenant.name != TENANT_NAME or tenant.slug != TENANT_SLUG:
            tenant.name = TENANT_NAME
            tenant.slug = TENANT_SLUG

        db.commit()
        db.refresh(tenant)
        return tenant
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _seed_content() -> None:
    for section in SECTIONS:
        content_path = f"content/{TENANT_SLUG}/{section}_v1.json"
        seed_tenant_content(
            tenant_key_or_name=TENANT_SLUG,
            section_key=section,
            slug=section,
            content_path=content_path,
            schema_version_cli=None,
            publish=True,
            replace=False,
        )


def _sync_section_names() -> None:
    db: Session = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if tenant is None:
            raise RuntimeError(f"Tenant not found: {TENANT_SLUG}")

        for key, label in SECTION_LABELS.items():
            section = db.scalar(
                select(Section).where(
                    Section.tenant_id == tenant.id,
                    Section.key == key,
                )
            )
            if section is not None and section.name != label:
                section.name = label

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run() -> None:
    tenant = _ensure_tenant()
    seed_tenant_schemas(
        tenant_key_or_name=TENANT_SLUG,
        base_dir="app/schemas",
        set_active=[],
        dry_run=False,
    )
    _sync_section_names()
    _seed_content()
    seed_tenant_settings(
        tenant_slug=TENANT_SLUG,
        contact_email=CONTACT_EMAIL,
        publish=True,
    )
    print(f"[jiribilla] Done. Tenant id={tenant.id}, slug={TENANT_SLUG}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap the Jiribilla tenant.")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
