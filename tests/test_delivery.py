from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import get_db as original_get_db
from app.models.auth import Tenant
from app.services.content_service import create_section, add_schema_version, create_entry
from app.services.publish_service import transition_entry_status
from app.schemas.content import EntryCreate

client = TestClient(app)


def _override_get_db_factory(db: Session):
    def _override_get_db():
        yield db
    return _override_get_db


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
                "properties": {
                    "title": {"type": "string"}
                },
                "required": ["title"]
            }
        },
        "required": ["hero"]
    }
    add_schema_version(
        db,
        tenant_id=tenant_id,
        section_id=section.id,
        version=1,
        schema=schema,
        title="v1",
        is_active=True,
    )
    payload = EntryCreate(
        tenant_id=tenant_id,
        section_id=section.id,
        slug=slug,
        schema_version=1,
        data={"hero": {"title": "Hola"}},
    )
    entry = create_entry(db, payload)
    return section, entry


def test_delivery_list_empty_is_ok(db: Session):
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db)
    r = client.get("/delivery/v1/entries?tenant_slug=latente")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert isinstance(body["items"], list)


def test_delivery_list_only_published(db: Session):
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db)
    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id, section_key="LandingPages", slug="home")
    db.commit()

    # draft no aparece
    r0 = client.get("/delivery/v1/entries?tenant_slug=latente&section_key=LandingPages")
    assert r0.status_code == 200
    assert r0.json()["total"] == 0

    # publicar → aparece
    transition_entry_status(db, e, "published")
    db.commit()
    r1 = client.get("/delivery/v1/entries?tenant_slug=latente&section_key=LandingPages")
    assert r1.status_code == 200
    body = r1.json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "home"
    assert body["items"][0]["status"] == "published"


def test_delivery_detail_published_and_draft_404(db: Session):
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db)
    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id, section_key="LandingPages", slug="home")
    db.commit()

    # draft → 404
    r0 = client.get("/delivery/v1/tenants/latente/sections/LandingPages/entries/home")
    assert r0.status_code == 404

    # published → 200
    transition_entry_status(db, e, "published")
    db.commit()
    r1 = client.get("/delivery/v1/tenants/latente/sections/LandingPages/entries/home")
    assert r1.status_code == 200
    assert r1.json()["slug"] == "home"


def test_delivery_etag_list_and_detail(db: Session):
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db)
    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id, section_key="LandingPages", slug="home")
    transition_entry_status(db, e, "published")
    db.commit()

    # Lista con ETag → 304
    r1 = client.get("/delivery/v1/entries?tenant_slug=latente&section_key=LandingPages")
    assert r1.status_code == 200
    etag = r1.headers.get("ETag")
    assert etag
    r1b = client.get(
        "/delivery/v1/entries?tenant_slug=latente&section_key=LandingPages",
        headers={"If-None-Match": etag},
    )
    assert r1b.status_code == 304

    # Detalle con ETag → 304
    r2 = client.get("/delivery/v1/tenants/latente/sections/LandingPages/entries/home")
    assert r2.status_code == 200
    etag2 = r2.headers.get("ETag")
    assert etag2
    r2b = client.get(
        "/delivery/v1/tenants/latente/sections/LandingPages/entries/home",
        headers={"If-None-Match": etag2},
    )
    assert r2b.status_code == 304
