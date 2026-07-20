from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema
from app.services import site_payload_service as sps

client = TestClient(app)


def _tenant(db: Session, slug: str = "jiribilla", name: str = "Jiribilla") -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if t is None:
        t = Tenant(slug=slug, name=name, is_active=True)
        db.add(t)
        db.flush()
    t.is_active = True
    db.flush()
    return t


def _section_with_entry(db: Session, tenant, key, data, *, schema=None, status="published"):
    section = db.scalar(
        select(Section).where(Section.tenant_id == tenant.id, Section.key == key)
    )
    if section is None:
        section = Section(tenant_id=tenant.id, key=key, name=key.title())
        db.add(section)
        db.flush()
        db.add(
            SectionSchema(
                tenant_id=tenant.id,
                section_id=section.id,
                version=1,
                title=f"{key} v1",
                schema=schema or {"type": "object"},
                is_active=True,
            )
        )
    entry = Entry(
        tenant_id=tenant.id,
        section_id=section.id,
        slug=key,
        schema_version=1,
        status=status,
        data=data,
    )
    db.add(entry)
    db.flush()
    return section, entry


def test_leaf_sections_become_blocks_keyed_by_section_key(db: Session):
    t = _tenant(db)
    _section_with_entry(db, t, "zz_hero_leaf", {"heroText": "Hola"})

    r = client.get(f"/delivery/v1/sites/{t.slug}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant"] == {"slug": t.slug, "name": t.name}
    assert body["blocks"]["zz_hero_leaf"] == {"heroText": "Hola"}


def test_container_section_spreads_its_blocks(db: Session):
    t = _tenant(db)
    _section_with_entry(
        db,
        t,
        "zz_container",
        {"zz_a": {"text": "A"}, "zz_b": {"text": "B"}},
        schema={"type": "object", "x-ui": {"container": True}},
    )

    body = client.get(f"/delivery/v1/sites/{t.slug}").json()
    assert body["blocks"]["zz_a"] == {"text": "A"}
    assert body["blocks"]["zz_b"] == {"text": "B"}
    assert "zz_container" not in body["blocks"]


def test_drafts_private_sections_and_internal_keys_are_excluded(db: Session):
    t = _tenant(db)
    _section_with_entry(db, t, "zz_pub", {"v": "Publicado", "__draft": {"v": "Borrador"}})
    _section_with_entry(db, t, "zz_draft", {"v": "x"}, status="draft")
    _section_with_entry(db, t, "settings", {"contact_email": "a@b.com"})
    _section_with_entry(db, t, "mensajes_eventos", {"title": "Mensajes"})

    blocks = client.get(f"/delivery/v1/sites/{t.slug}").json()["blocks"]
    assert blocks["zz_pub"] == {"v": "Publicado"}
    assert "zz_draft" not in blocks
    assert "settings" not in blocks
    assert "mensajes_eventos" not in blocks


def test_container_block_wins_over_a_same_named_leaf_section(db: Session):
    """Keeps the payload stable while a tenant is mid-consolidation."""
    t = _tenant(db)
    _section_with_entry(db, t, "zz_dup", {"v": "leaf"})
    _section_with_entry(
        db,
        t,
        "zz_dup_container",
        {"zz_dup": {"v": "container"}},
        schema={"type": "object", "x-ui": {"container": True}},
    )

    blocks = client.get(f"/delivery/v1/sites/{t.slug}").json()["blocks"]
    assert blocks["zz_dup"] == {"v": "container"}


def test_non_allowlisted_tenant_is_404_even_if_it_exists(db: Session):
    """Other tenants' content must never be exposed through this aggregate."""
    other = _tenant(db, slug="owa", name="OWA")
    _section_with_entry(db, other, "zz_owa_block", {"v": "secreto"})
    assert "owa" not in sps.SITE_PAYLOAD_TENANTS
    assert client.get("/delivery/v1/sites/owa").status_code == 404


def test_unknown_tenant_is_404(db: Session):
    assert client.get("/delivery/v1/sites/no-existe-jamas").status_code == 404


def test_inactive_tenant_is_404(db: Session):
    t = _tenant(db)
    t.is_active = False
    db.flush()
    assert client.get(f"/delivery/v1/sites/{t.slug}").status_code == 404


def test_etag_returns_304(db: Session):
    t = _tenant(db)
    _section_with_entry(db, t, "zz_etag", {"v": "1"})

    first = client.get(f"/delivery/v1/sites/{t.slug}")
    etag = first.headers.get("ETag")
    assert etag
    second = client.get(f"/delivery/v1/sites/{t.slug}", headers={"If-None-Match": etag})
    assert second.status_code == 304
