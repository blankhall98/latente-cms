from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.auth import Tenant
from app.services.content_service import add_schema_version, create_section


@pytest.fixture
def client():
    return TestClient(app)


def _seed_minimal_schema(db: Session) -> tuple[int, int, dict]:
    tenant = Tenant(slug=f"ui-schema-{uuid.uuid4().hex[:8]}", name="UI Schema Tenant")
    db.add(tenant)
    db.flush()

    section = create_section(
        db,
        tenant_id=tenant.id,
        key="LandingPages",
        name="Landing Pages",
    )

    schema_v1 = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "LandingPage@1",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hero": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 120},
                    "subtitle": {"type": "string", "maxLength": 200},
                    "background_image": {"type": "string", "format": "uri"},
                },
                "required": ["title"],
            },
            "seo": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 60},
                    "description": {"type": "string", "maxLength": 160},
                },
            },
        },
        "required": ["hero"],
    }

    add_schema_version(
        db,
        tenant_id=tenant.id,
        section_id=section.id,
        version=1,
        schema=schema_v1,
        title="LandingPages v1",
        is_active=True,
    )
    db.flush()
    return int(tenant.id), int(section.id), schema_v1


def test_ui_schema_endpoint_ok(client: TestClient, db_session: Session, auth_headers):
    tenant_id, section_id, schema_v1 = _seed_minimal_schema(db_session)
    headers = auth_headers(
        user_id=123,
        tenant_id=tenant_id,
        permissions=("content:read",),
    )

    resp = client.get(
        f"/api/v1/schemas/{section_id}/active/ui",
        params={"tenant_id": tenant_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["section_id"] == section_id
    assert data["tenant_id"] == tenant_id
    assert data["active_version"] == 1
    assert data["title"] == "LandingPages v1"
    assert data["schema"] == schema_v1
    assert data["widgets"] == {}
    assert data["hints"] == {}


def test_ui_schema_404_without_active(client: TestClient, db_session: Session, auth_headers):
    tenant = Tenant(slug=f"noactive-{uuid.uuid4().hex[:8]}", name="No Active")
    db_session.add(tenant)
    db_session.flush()
    section = create_section(db_session, tenant_id=tenant.id, key="Foo", name="Foo")
    headers = auth_headers(
        user_id=123,
        tenant_id=tenant.id,
        permissions=("content:read",),
    )

    resp = client.get(
        f"/api/v1/schemas/{section.id}/active/ui",
        params={"tenant_id": tenant.id},
        headers=headers,
    )
    assert resp.status_code == 404
