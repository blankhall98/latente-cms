from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models.audit import ContentAction, ContentAuditLog
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
    tenant = Tenant(slug=f"audit-{uuid.uuid4().hex[:8]}", name="Audit Tenant")
    db_session.add(tenant)
    db_session.flush()

    section = create_section(
        db_session,
        tenant_id=tenant.id,
        key="LandingPages",
        name="Landing Pages",
    )
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


def test_audit_on_create_entry(
    client: TestClient,
    db_session: Session,
    content_scope: tuple[int, int],
    auth_headers,
):
    tenant_id, section_id = content_scope
    headers = auth_headers(
        user_id=123,
        tenant_id=tenant_id,
        permissions=("content:write",),
    )
    payload = {
        "tenant_id": tenant_id,
        "section_id": section_id,
        "schema_version": 1,
        "data": {"slug": "home", "title": "Home"},
    }

    r = client.post("/api/v1/content/entries", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    logs = db_session.query(ContentAuditLog).filter_by(entry_id=entry_id).all()
    assert len(logs) >= 1
    log = logs[0]
    assert log.action == ContentAction.CREATE
    assert log.user_id == 123
    assert log.details.get("slug") in ("home", None)


def test_audit_on_update_entry(
    client: TestClient,
    db_session: Session,
    content_scope: tuple[int, int],
    auth_headers,
):
    tenant_id, section_id = content_scope
    headers = auth_headers(
        user_id=9,
        tenant_id=tenant_id,
        permissions=("content:write",),
    )
    r = client.post(
        "/api/v1/content/entries",
        json={
            "tenant_id": tenant_id,
            "section_id": section_id,
            "schema_version": 1,
            "data": {"slug": "about", "title": "About"},
        },
        headers=headers,
    )
    assert r.status_code == 201
    entry_id = r.json()["id"]

    r2 = client.patch(
        f"/api/v1/content/entries/{entry_id}",
        json={"tenant_id": tenant_id, "data": {"title": "About us"}},
        headers=headers,
    )
    assert r2.status_code == 200

    logs = (
        db_session.query(ContentAuditLog)
        .filter_by(entry_id=entry_id)
        .order_by(ContentAuditLog.created_at.asc())
        .all()
    )
    assert len(logs) >= 2
    last = logs[-1]
    assert last.action == ContentAction.UPDATE
    assert "changed_keys" in last.details
    assert "title" in last.details["changed_keys"]


def test_audit_on_publish(
    client: TestClient,
    db_session: Session,
    content_scope: tuple[int, int],
    auth_headers,
):
    tenant_id, section_id = content_scope
    headers = auth_headers(
        user_id=42,
        tenant_id=tenant_id,
        permissions=("content:write", "content:publish"),
    )
    r = client.post(
        "/api/v1/content/entries",
        json={
            "tenant_id": tenant_id,
            "section_id": section_id,
            "schema_version": 1,
            "data": {"slug": "news"},
        },
        headers=headers,
    )
    assert r.status_code == 201
    entry_id = r.json()["id"]

    rp = client.post(
        f"/api/v1/content/entries/{entry_id}/publish",
        json={"tenant_id": tenant_id},
        headers=headers,
    )
    assert rp.status_code == 200

    logs = (
        db_session.query(ContentAuditLog)
        .filter_by(entry_id=entry_id, action=ContentAction.PUBLISH)
        .all()
    )
    assert len(logs) == 1
    log = logs[0]
    assert log.user_id == 42
    assert log.details.get("after_status") == "published"
