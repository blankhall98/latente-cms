from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.auth import Tenant
from app.schemas.content import EntryCreate
from app.services.content_service import add_schema_version, create_entry, create_section


client = TestClient(app)


def _mk_tenant(db: Session) -> Tenant:
    tenant = Tenant(name=f"T-{uuid.uuid4().hex[:8]}", slug=f"t-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _mk_section_and_schema(db: Session, tenant_id: int):
    section = create_section(db, tenant_id=tenant_id, key="LandingPages", name="Landing Pages")
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
        db,
        tenant_id=tenant_id,
        section_id=section.id,
        version=1,
        schema=schema,
        title="v1",
        is_active=True,
    )
    return section


def _mk_entry(db: Session, tenant_id: int, section_id: int):
    payload = EntryCreate(
        tenant_id=tenant_id,
        section_id=section_id,
        slug="home",
        schema_version=1,
        status="draft",
        data={"hero": {"title": "Hola"}},
    )
    entry = create_entry(db, payload)
    db.flush()
    return entry


def test_publish_unpublish_archive_and_preview(db: Session, auth_headers):
    tenant = _mk_tenant(db)
    section = _mk_section_and_schema(db, tenant.id)
    entry = _mk_entry(db, tenant.id, section.id)
    headers = auth_headers(
        user_id=1,
        tenant_id=tenant.id,
        permissions=("content:publish",),
    )

    r = client.post(f"/api/v1/content/entries/{entry.id}/publish?tenant_id={tenant.id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    assert r.json()["published_at"] is not None

    r2 = client.get(f"/api/v1/content/entries/{entry.id}/preview?tenant_id={tenant.id}", headers=headers)
    assert r2.status_code == 200
    assert "ETag" in r2.headers
    assert "Cache-Control" in r2.headers
    etag = r2.headers["ETag"]

    r3 = client.get(
        f"/api/v1/content/entries/{entry.id}/preview?tenant_id={tenant.id}",
        headers={**headers, "If-None-Match": etag},
    )
    assert r3.status_code == 304

    r4 = client.post(f"/api/v1/content/entries/{entry.id}/unpublish?tenant_id={tenant.id}", headers=headers)
    assert r4.status_code == 200
    assert r4.json()["status"] == "draft"

    r5 = client.post(f"/api/v1/content/entries/{entry.id}/archive?tenant_id={tenant.id}", headers=headers)
    assert r5.status_code == 200
    assert r5.json()["status"] == "archived"
    assert r5.json()["archived_at"] is not None
