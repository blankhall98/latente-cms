# tests/test_webhooks.py
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.main import app
from app.core.config import settings
from app.db.session import get_db
from app.models.content import Section, SectionSchema

# Helpers mínimos para crear sección + schema (get-or-create, idempotente)
def _mk_section_and_schema(db, tenant_id: int, key: str = "LandingPages"):
    from app.models.content import Section, SectionSchema

    # --- get-or-create Section ---
    sec = db.scalar(
        select(Section).where(
            Section.tenant_id == tenant_id,
            Section.key == key,
        )
    )
    if not sec:
        sec = Section(
            tenant_id=tenant_id,
            key=key,
            name="LP",
            description=None,
        )
        db.add(sec)
        db.flush()  # obtiene sec.id

    # --- get-or-create SectionSchema v1 ---
    ss = db.scalar(
        select(SectionSchema).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == sec.id,
            SectionSchema.version == 1,
        )
    )
    if not ss:
        ss = SectionSchema(
            tenant_id=tenant_id,
            section_id=sec.id,
            version=1,
            title="LP v1",
            schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            is_active=True,
        )
        db.add(ss)
        db.flush()

    return sec, ss

def _override_get_db_factory(db: Session):
    def _get_db():
        yield db
    return _get_db

client = TestClient(app)
original_get_db = get_db

def _auth(user_id: int):
    return {"X-User-Id": str(user_id)}

@pytest.mark.parametrize("event_name", ["content.published", "content.unpublished", "content.archived"])
def test_webhook_fires_on_state_change(monkeypatch, db_session: Session, event_name: str):
    # Forzar modo sync para que el envío ocurra dentro del test
    monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOKS_SYNC_FOR_TEST", True)

    # Interceptar endpoints para no depender de DB ni tabla
    captured: dict = {"calls": []}

    def fake_get_endpoints_for_tenant(db, tenant_id: int):
        return [{"url": "http://example.com/webhook", "secret": "topsecret", "events": [event_name]}]

    async def fake_deliver_with_retries(url, headers, body, timeout, max_retries, backoff_seconds):
        # Validaciones mínimas
        assert url == "http://example.com/webhook"
        assert headers.get("X-Webhook-Event") == event_name
        assert headers.get("X-Webhook-Timestamp")
        assert headers.get("X-Webhook-Signature")
        # Cuerpo parseable JSON
        payload = json.loads(body.decode("utf-8"))
        assert "tenant_id" in payload and "entry_id" in payload and "slug" in payload
        captured["calls"].append((url, headers, payload))
        return True, 200

    # Monkeypatch de servicio
    import app.services.webhook_service as wh
    monkeypatch.setattr(wh, "get_endpoints_for_tenant", fake_get_endpoints_for_tenant)
    monkeypatch.setattr(wh, "_deliver_with_retries", fake_deliver_with_retries)

    # **Nuevo**: monkeypatch de permisos para este test (no valida RBAC)
    import app.api.deps.auth as auth_deps
    monkeypatch.setattr(auth_deps, "user_has_permission", lambda db, user_id, tenant_id, perm_key: True)

    # Preparar DB y app
    app.dependency_overrides[original_get_db] = _override_get_db_factory(db_session)

    # Crear tenant mínimo (usamos uno existente del seed del proyecto o creamos rápido)
    # Aquí suponemos un tenant 1 "latente" ya existe por seeds previos; si no, crea uno sencillo.
    tenant_id = 1

    # Sección + schema
    sec, ss = _mk_section_and_schema(db_session, tenant_id, key="LandingPages")

    # Crear entry via API (estado draft)
    r = client.post(
        "/api/v1/content/entries",
        json={
            "tenant_id": tenant_id,
            "section_id": sec.id,
            "schema_version": 1,
            "data": {"slug": "home", "title": "Index"},
        },
        headers=_auth(7),
    )
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    # Disparar evento según el caso
    if event_name == "content.published":
        r2 = client.post(f"/api/v1/content/entries/{entry_id}/publish?tenant_id={tenant_id}", headers=_auth(7))
        assert r2.status_code == 200, r2.text
    elif event_name == "content.unpublished":
        # publish primero para luego unpublish
        client.post(f"/api/v1/content/entries/{entry_id}/publish?tenant_id={tenant_id}", headers=_auth(7))
        r2 = client.post(f"/api/v1/content/entries/{entry_id}/unpublish?tenant_id={tenant_id}", headers=_auth(7))
        assert r2.status_code == 200, r2.text
    else:  # archived
        r2 = client.post(f"/api/v1/content/entries/{entry_id}/archive?tenant_id={tenant_id}", headers=_auth(7))
        assert r2.status_code == 200, r2.text

    # Verificamos que el envío se haya intentado exactamente 1 vez
    assert len(captured["calls"]) == 1
    url, headers, payload = captured["calls"][0]
    assert headers["X-Webhook-Event"] == event_name
    # Timestamp en ISO en payload
    assert payload["timestamp"].endswith("Z")

