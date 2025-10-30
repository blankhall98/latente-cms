# tests/test_preview_tokens.py
# ⟶ Tests de token válido, expirado y firma inválida
from __future__ import annotations
import time
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import SessionLocal, get_db as original_get_db
from app.models.auth import Tenant
from app.services.content_service import create_section, add_schema_version, create_entry
from app.schemas.content import EntryCreate
from app.security.preview_tokens import create_preview_token

client = TestClient(app)

@pytest.fixture()
def db() -> Session:
    s = SessionLocal()
    tx = s.begin()
    try:
        yield s
    finally:
        try:
            if tx.is_active:
                tx.rollback()
        except Exception:
            pass
        s.close()

def _override_get_db_factory(db: Session):
    def _override():
        yield db
    return _override

def _mk_tenant(db: Session) -> Tenant:
    t = Tenant(name=f"T-{uuid.uuid4().hex[:6]}", slug=f"t-{uuid.uuid4().hex[:6]}")
    db.add(t); db.flush()
    return t

def _mk_section_schema_entry(db: Session, tenant_id: int):
    section = create_section(db, tenant_id=tenant_id, key="LandingPages", name="Landing Pages")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"hero": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
        "required": ["hero"]
    }
    add_schema_version(db, tenant_id=tenant_id, section_id=section.id, version=1, schema=schema, title="v1", is_active=True)
    entry = create_entry(db, EntryCreate(
        tenant_id=tenant_id, section_id=section.id, slug="home",
        schema_version=1, status="draft", data={"hero": {"title": "Hola"}}))
    db.flush()
    return section, entry

def test_preview_with_valid_token(db: Session, monkeypatch):
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db)

    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id)
    db.commit()

    token = create_preview_token(tenant_id=t.id, entry_id=e.id, schema_version=1, expires_in=300)

    r = client.get(f"/api/v1/content/entries/{e.id}/preview?token={token}")
    assert r.status_code == 200
    assert "ETag" in r.headers

    app.dependency_overrides.pop(original_get_db, None)

def test_preview_with_expired_token(db: Session):
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db)

    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id)
    db.commit()

    token = create_preview_token(tenant_id=t.id, entry_id=e.id, schema_version=1, expires_in=1)
    time.sleep(1.2)

    r = client.get(f"/api/v1/content/entries/{e.id}/preview?token={token}")
    assert r.status_code in (401, 403)

    app.dependency_overrides.pop(original_get_db, None)

def test_issue_preview_token_requires_auth(db: Session):
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db)

    t = _mk_tenant(db)
    _, e = _mk_section_schema_entry(db, t.id)
    db.commit()

    # sin auth (sin X-User-Id) → 401
    r = client.post(f"/api/v1/content/entries/{e.id}/preview-token?tenant_id={t.id}")
    assert r.status_code == 401

    app.dependency_overrides.pop(original_get_db, None)
