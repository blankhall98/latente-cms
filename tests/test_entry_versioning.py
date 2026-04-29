from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models.auth import Tenant
from app.services.content_service import add_schema_version, create_section


@pytest.fixture
def client(db_session: Session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def content_scope(db_session: Session) -> tuple[int, int]:
    tenant = Tenant(slug=f"version-{uuid.uuid4().hex[:8]}", name="Version Tenant")
    db_session.add(tenant)
    db_session.flush()

    section = create_section(db_session, tenant_id=tenant.id, key="LandingPages", name="Landing Pages")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "title": {"type": "string"},
        },
    }
    add_schema_version(
        db_session,
        tenant_id=tenant.id,
        section_id=section.id,
        version=1,
        schema=schema,
        title="v1",
        is_active=True,
    )
    return int(tenant.id), int(section.id)


def test_versioning_flow(
    client: TestClient,
    content_scope: tuple[int, int],
    auth_headers,
):
    tenant_id, section_id = content_scope
    headers = auth_headers(
        user_id=1,
        tenant_id=tenant_id,
        permissions=("content:write",),
    )

    r = client.post(
        "/api/v1/content/entries",
        json={
            "tenant_id": tenant_id,
            "section_id": section_id,
            "schema_version": 1,
            "data": {"slug": "vflow", "title": "VFlow"},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    v = client.get(
        f"/api/v1/content/entries/{entry_id}/versions",
        params={"tenant_id": tenant_id},
        headers=headers,
    )
    assert v.status_code == 200
    versions = v.json()
    assert len(versions) == 1
    assert versions[0]["version_idx"] == 1
    assert versions[0]["reason"] == "create"

    r2 = client.patch(
        f"/api/v1/content/entries/{entry_id}",
        json={"tenant_id": tenant_id, "data": {"title": "VFlow 2"}},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text

    v2 = client.get(
        f"/api/v1/content/entries/{entry_id}/versions",
        params={"tenant_id": tenant_id},
        headers=headers,
    )
    assert v2.status_code == 200
    versions2 = v2.json()
    assert len(versions2) == 2
    assert versions2[-1]["version_idx"] == 2
    assert versions2[-1]["reason"] == "update"

    restore_headers = auth_headers(
        user_id=9,
        tenant_id=tenant_id,
        permissions=("content:write",),
    )
    rr = client.post(
        f"/api/v1/content/entries/{entry_id}/versions/1/restore",
        json={"tenant_id": tenant_id},
        headers=restore_headers,
    )
    assert rr.status_code == 200, rr.text

    v3 = client.get(
        f"/api/v1/content/entries/{entry_id}/versions",
        params={"tenant_id": tenant_id},
        headers=headers,
    )
    assert v3.status_code == 200
    versions3 = v3.json()
    assert len(versions3) == 3
    assert versions3[-1]["version_idx"] == 3
    assert versions3[-1]["reason"] == "restore"
