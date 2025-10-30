# tests/test_delivery_cache.py
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.auth import Tenant
from app.services.content_service import create_section, add_schema_version, create_entry
from app.services.publish_service import transition_entry_status
from app.schemas.content import EntryCreate

client = TestClient(app)


def _mk_tenant(db: Session, slug="latente", name="Latente"):
    t = Tenant(slug=slug, name=name, is_active=True)
    db.add(t)
    db.flush()
    return t


def _mk_section_schema_entry(db: Session, tenant_id: int, section_key="LandingPages", slug="home"):
    section = create_section(db, tenant_id=tenant_id, key=section_key, name="Landing Pages")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "hero": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            }
        },
        "required": ["hero"],
    }
    add_schema_version(
        db, tenant_id=tenant_id, section_id=section.id, version=1, schema=schema, title="v1", is_active=True
    )
    entry = create_entry(
        db,
        EntryCreate(
            tenant_id=tenant_id,
            section_id=section.id,
            slug=slug,
            schema_version=1,
            data={"hero": {"title": "Hola"}},
        ),
    )
    return section, entry


def test_list_if_modified_since_304(db: Session):
    # Arrange: 1 entry publicado
    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id, section_key="LandingPages", slug="home")
    transition_entry_status(db, e, "published")
    db.commit()

    # Primera llamada (200) — obtenemos Last-Modified + ETag
    r1 = client.get("/delivery/v1/entries?tenant_slug=latente&section_key=LandingPages")
    assert r1.status_code == 200
    lm = r1.headers.get("Last-Modified")
    etag = r1.headers.get("ETag")
    assert lm is not None
    assert etag is not None

    # If-Modified-Since → 304 (sin cambios)
    r2 = client.get(
        "/delivery/v1/entries?tenant_slug=latente&section_key=LandingPages",
        headers={"If-Modified-Since": lm},
    )
    assert r2.status_code == 304
    # Debe mantener headers de cache avanzados
    assert "ETag" in r2.headers
    assert "Cache-Control" in r2.headers


def test_detail_if_modified_since_304(db: Session):
    # Arrange: 1 entry publicado
    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id, section_key="LandingPages", slug="home")
    transition_entry_status(db, e, "published")
    db.commit()

    # Primera llamada (200)
    url = "/delivery/v1/tenants/latente/sections/LandingPages/entries/home"
    r1 = client.get(url)
    assert r1.status_code == 200
    lm = r1.headers.get("Last-Modified")
    etag = r1.headers.get("ETag")
    assert lm is not None
    assert etag is not None

    # If-Modified-Since → 304
    r2 = client.get(url, headers={"If-Modified-Since": lm})
    assert r2.status_code == 304
    assert "ETag" in r2.headers
    assert "Cache-Control" in r2.headers


def test_cache_control_policies_list_vs_detail(db: Session):
    # Arrange
    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id, section_key="LandingPages", slug="home")
    transition_entry_status(db, e, "published")
    db.commit()

    # Lista → cache corta (max-age=60, swr=120)
    r_list = client.get("/delivery/v1/entries?tenant_slug=latente&section_key=LandingPages")
    assert r_list.status_code == 200
    cc_list = r_list.headers.get("Cache-Control", "")
    assert "public" in cc_list
    assert "max-age=60" in cc_list
    assert "stale-while-revalidate=120" in cc_list

    # Detalle → cache más larga (max-age=300, swr=600)
    r_det = client.get("/delivery/v1/tenants/latente/sections/LandingPages/entries/home")
    assert r_det.status_code == 200
    cc_det = r_det.headers.get("Cache-Control", "")
    assert "public" in cc_det
    assert "max-age=300" in cc_det
    assert "stale-while-revalidate=600" in cc_det
