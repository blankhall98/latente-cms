"""
Guards the highest-priority constraint of the Jiribilla consolidation work:
no behavioural or data change for any other tenant.

These assertions compare against a committed baseline, so they fail loudly if
Jiribilla-scoped work leaks into ANRO, OWA, DEWA or Ragni-Grady.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import Tenant
from app.models.content import Section, SectionSchema
from app.services.site_payload_service import SITE_PAYLOAD_TENANTS, is_container_schema

OTHER_TENANTS = ["anro", "owa", "dewa", "ragni-grady"]
BASELINE = Path(__file__).parent / "fixtures" / "tenant_isolation_baseline.json"


def _section_keys(db: Session, slug: str) -> list[str]:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        pytest.skip(f"tenant {slug} not seeded in this database")
    return sorted(
        key
        for (key,) in db.execute(
            select(Section.key).where(Section.tenant_id == tenant.id)
        ).all()
    )


@pytest.mark.parametrize("slug", OTHER_TENANTS)
def test_other_tenants_section_keys_match_baseline(db: Session, slug: str):
    """Fails if Jiribilla work adds, renames or removes another tenant's sections."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if slug not in baseline:
        pytest.skip(f"{slug} not captured in the baseline")
    assert _section_keys(db, slug) == baseline[slug]


@pytest.mark.parametrize("slug", OTHER_TENANTS)
def test_other_tenants_have_no_container_sections(db: Session, slug: str):
    """Container schemas change how delivery groups content — Jiribilla only."""
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        pytest.skip(f"tenant {slug} not seeded in this database")
    schemas = db.scalars(
        select(SectionSchema).where(SectionSchema.tenant_id == tenant.id)
    ).all()
    offenders = sorted({s.section_id for s in schemas if is_container_schema(s.schema)})
    assert offenders == [], f"{slug} gained container sections: {offenders}"


@pytest.mark.parametrize("slug", OTHER_TENANTS)
def test_other_tenants_are_not_exposed_by_the_site_endpoint(slug: str):
    """The whole-site aggregate is opt-in; no other project gains that surface."""
    assert slug not in SITE_PAYLOAD_TENANTS
