from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
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
    tenant = Tenant(slug=f"limit-{uuid.uuid4().hex[:8]}", name="Limit Tenant")
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


def test_payload_cap(
    client: TestClient,
    monkeypatch,
    content_scope: tuple[int, int],
    auth_headers,
):
    tenant_id, section_id = content_scope
    monkeypatch.setattr(settings, "MAX_ENTRY_DATA_KB", 1)
    big_text = "x" * 4096
    headers = auth_headers(
        user_id=101,
        tenant_id=tenant_id,
        permissions=("content:write",),
    )

    r = client.post(
        "/api/v1/content/entries",
        json={
            "tenant_id": tenant_id,
            "section_id": section_id,
            "schema_version": 1,
            "data": {"slug": "big", "title": big_text},
        },
        headers=headers,
    )
    assert r.status_code == 413
    assert "Payload too large" in r.text


def test_idempotency_create(
    client: TestClient,
    monkeypatch,
    content_scope: tuple[int, int],
    auth_headers,
):
    tenant_id, section_id = content_scope
    monkeypatch.setattr(settings, "IDEMPOTENCY_ENABLED", True)
    key = f"test-key-{uuid.uuid4().hex}"
    headers = {
        **auth_headers(user_id=102, tenant_id=tenant_id, permissions=("content:write",)),
        "Idempotency-Key": key,
    }

    body = {
        "tenant_id": tenant_id,
        "section_id": section_id,
        "schema_version": 1,
        "data": {"slug": "idem", "title": "A"},
    }
    r1 = client.post("/api/v1/content/entries", json=body, headers=headers)
    assert r1.status_code == 201
    e1 = r1.json()["id"]

    r2 = client.post("/api/v1/content/entries", json=body, headers=headers)
    assert r2.status_code == r1.status_code
    assert r2.json()["id"] == e1
    assert r2.headers.get("Idempotent-Replay") == "true"


def test_rate_limit_writes(
    client: TestClient,
    monkeypatch,
    content_scope: tuple[int, int],
    auth_headers,
):
    tenant_id, section_id = content_scope
    monkeypatch.setattr(settings, "RATELIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATELIMIT_WRITE_PER_MIN", 3)
    headers = auth_headers(
        user_id=103,
        tenant_id=tenant_id,
        permissions=("content:write",),
    )

    for i in range(3):
        r = client.post(
            "/api/v1/content/entries",
            json={
                "tenant_id": tenant_id,
                "section_id": section_id,
                "schema_version": 1,
                "data": {"slug": f"rl{i}", "title": "ok"},
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text

    r4 = client.post(
        "/api/v1/content/entries",
        json={
            "tenant_id": tenant_id,
            "section_id": section_id,
            "schema_version": 1,
            "data": {"slug": "rl3", "title": "blocked"},
        },
        headers=headers,
    )
    assert r4.status_code == 429
    assert "Rate limit exceeded" in r4.text
