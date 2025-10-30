# tests/test_ui_schema_endpoint.py
from __future__ import annotations
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import get_db
from app.db.session import SessionLocal
from app.services.content_service import create_section, add_schema_version
from app.models.auth import Tenant

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _seed_minimal_schema(db: Session) -> tuple[int, int]:
    # Tenant
    t = db.query(Tenant).filter(Tenant.slug == "latente").first()
    if not t:
        t = Tenant(slug="latente", name="Latente Example")
        db.add(t)
        db.flush()
    tenant_id = t.id

    # Section
    section = create_section(
        db, tenant_id=tenant_id, key="LandingPages", name="Landing Pages"
    )
    db.flush()

    # JSON Schema simple
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
                    "background_image": {"type": "string", "format": "uri"}
                },
                "required": ["title"]
            },
            "seo": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 60},
                    "description": {"type": "string", "maxLength": 160}
                }
            }
        },
        "required": ["hero"]
    }

    add_schema_version(
        db,
        tenant_id=tenant_id,
        section_id=section.id,
        version=1,
        schema=schema_v1,
        title="LandingPages v1",
        is_active=True,
    )
    db.commit()
    return tenant_id, section.id

def test_ui_schema_endpoint_ok(client: TestClient, db: Session, monkeypatch):
    # Fuerza permiso True
    from app.api.deps import auth as deps_auth
    monkeypatch.setattr(deps_auth, "user_has_permission", lambda *args, **kwargs: True)

    tenant_id, section_id = _seed_minimal_schema(db)

    headers = {"X-User-Id": "123"}  # identidad básica (tu dep ya lo usa)
    resp = client.get(
        f"/api/v1/schemas/{section_id}/active/ui",
        params={"tenant_id": tenant_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["section_id"] == section_id
    assert data["schema_version"] == 1

    ui = data["ui_schema"]
    assert "fields" in ui and isinstance(ui["fields"], list) and len(ui["fields"]) >= 1

    hints = data["hints"]
    assert "required" in hints and "order" in hints
    assert "hero" in hints["required"]
    assert hints["order"][0] == "hero"

    pol = data["policy"]
    assert "max_entry_data_kb" in pol
    assert "idempotency_enabled" in pol

def test_ui_schema_404_without_active(client: TestClient, db: Session, monkeypatch):
    # Fuerza permiso True
    from app.api.deps import auth as deps_auth
    monkeypatch.setattr(deps_auth, "user_has_permission", lambda *args, **kwargs: True)

    # Crea tenant y sección sin activar schema
    t = db.query(Tenant).filter(Tenant.slug == "noactive").first()
    if not t:
        t = Tenant(slug="noactive", name="No Active")
        db.add(t)
        db.flush()
    section = create_section(db, tenant_id=t.id, key="Foo", name="Foo")
    db.commit()

    headers = {"X-User-Id": "123"}
    resp = client.get(
        f"/api/v1/schemas/{section.id}/active/ui",
        params={"tenant_id": t.id},
        headers=headers,
    )
    assert resp.status_code == 404
