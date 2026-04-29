from __future__ import annotations

import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.auth import Tenant
from app.schemas.content import EntryCreate
from app.security.preview_tokens import create_preview_token
from app.services.content_service import add_schema_version, create_entry, create_section


client = TestClient(app)


def _mk_tenant(db: Session) -> Tenant:
    tenant = Tenant(name=f"T-{uuid.uuid4().hex[:8]}", slug=f"t-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _mk_section_schema_entry(db: Session, tenant_id: int):
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
    entry = create_entry(
        db,
        EntryCreate(
            tenant_id=tenant_id,
            section_id=section.id,
            slug="home",
            schema_version=1,
            status="draft",
            data={"hero": {"title": "Hola"}},
        ),
    )
    db.flush()
    return section, entry


def test_preview_with_valid_token(db: Session):
    tenant = _mk_tenant(db)
    _, entry = _mk_section_schema_entry(db, tenant.id)
    token = create_preview_token(tenant_id=tenant.id, entry_id=entry.id, schema_version=1, expires_in=300)

    r = client.get(f"/api/v1/content/entries/{entry.id}/preview?token={token}")
    assert r.status_code == 200
    assert "ETag" in r.headers


def test_preview_with_expired_token(db: Session):
    tenant = _mk_tenant(db)
    _, entry = _mk_section_schema_entry(db, tenant.id)
    token = create_preview_token(tenant_id=tenant.id, entry_id=entry.id, schema_version=1, expires_in=1)
    time.sleep(1.2)

    r = client.get(f"/api/v1/content/entries/{entry.id}/preview?token={token}")
    assert r.status_code in (401, 403)


def test_issue_preview_token_requires_auth(db: Session):
    tenant = _mk_tenant(db)
    _, entry = _mk_section_schema_entry(db, tenant.id)

    r = client.post(f"/api/v1/content/entries/{entry.id}/preview-token?tenant_id={tenant.id}")
    assert r.status_code == 401
