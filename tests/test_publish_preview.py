# tests/test_publish_preview.py
# ⟶ Usa la MISMA sesión en el endpoint (override get_db) + header X-User-Id

from __future__ import annotations
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import SessionLocal
from app.api.deps import auth as auth_deps  # para monkeypatch
from app.db.session import get_db as original_get_db
from app.models.auth import Tenant
from app.services.content_service import create_section, add_schema_version, create_entry
from app.schemas.content import EntryCreate

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

def _mk_tenant(db: Session) -> Tenant:
    t = Tenant(name=f"T-{uuid.uuid4().hex[:6]}", slug=f"t-{uuid.uuid4().hex[:6]}")
    db.add(t); db.flush()
    return t

def _mk_section_and_schema(db: Session, tenant_id: int):
    section = create_section(db, tenant_id=tenant_id, key="LandingPages", name="Landing Pages")
    db.flush()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"hero": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
        "required": ["hero"]
    }
    add_schema_version(db, tenant_id=tenant_id, section_id=section.id, version=1, schema=schema, title="v1", is_active=True)
    return section

def _mk_entry(db: Session, tenant_id: int, section_id: int):
    payload = EntryCreate(
        tenant_id=tenant_id, section_id=section_id,
        slug="home", schema_version=1, status="draft",
        data={"hero": {"title": "Hola"}}
    )
    e = create_entry(db, payload); db.flush()
    return e

def test_publish_unpublish_archive_and_preview(db: Session, monkeypatch):
    # 1) Bypass de permisos para este test de flujo
    monkeypatch.setattr(auth_deps, "user_has_permission", lambda db, user_id, tenant_id, perm_key: True)

    # 2) Override get_db para que el endpoint use LA MISMA sesión
    def _override_get_db():
        yield db
    app.dependency_overrides[original_get_db] = _override_get_db

    t = _mk_tenant(db)
    s = _mk_section_and_schema(db, t.id)
    e = _mk_entry(db, t.id, s.id)
    db.commit()

    headers = {"X-User-Id": "1"}

    # publish
    r = client.post(f"/api/v1/content/entries/{e.id}/publish?tenant_id={t.id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    assert r.json()["published_at"] is not None

    # preview con ETag
    r2 = client.get(f"/api/v1/content/entries/{e.id}/preview?tenant_id={t.id}", headers=headers)
    assert r2.status_code == 200
    assert "ETag" in r2.headers
    assert "Cache-Control" in r2.headers
    etag = r2.headers["ETag"]

    # 304
    r3 = client.get(
        f"/api/v1/content/entries/{e.id}/preview?tenant_id={t.id}",
        headers={**headers, "If-None-Match": etag},
    )
    assert r3.status_code == 304

    # unpublish → draft
    r4 = client.post(f"/api/v1/content/entries/{e.id}/unpublish?tenant_id={t.id}", headers=headers)
    assert r4.status_code == 200
    assert r4.json()["status"] == "draft"

    # archive
    r5 = client.post(f"/api/v1/content/entries/{e.id}/archive?tenant_id={t.id}", headers=headers)
    assert r5.status_code == 200
    assert r5.json()["status"] == "archived"
    assert r5.json()["archived_at"] is not None

    # Limpia override para no afectar otros tests
    app.dependency_overrides.pop(original_get_db, None)


