from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.auth import Tenant
from app.models.content import Section, SectionSchema


def _mk_section_and_schema(db: Session, tenant_id: int, key: str = "LandingPages"):
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
        db.flush()

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


@pytest.mark.parametrize("event_name", ["content.published", "content.unpublished", "content.archived"])
def test_webhook_fires_on_state_change(monkeypatch, db_session: Session, auth_headers, event_name: str):
    monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOKS_SYNC_FOR_TEST", True)

    captured: dict = {"calls": []}

    def fake_get_endpoints_for_tenant(db, tenant_id: int):
        return [{"url": "http://example.com/webhook", "secret": "topsecret", "events": [event_name]}]

    async def fake_deliver_with_retries(url, headers, body, timeout, max_retries, backoff_seconds):
        assert url == "http://example.com/webhook"
        assert headers.get("X-Webhook-Event") == event_name
        assert headers.get("X-Webhook-Timestamp")
        assert headers.get("X-Webhook-Signature")
        payload = json.loads(body.decode("utf-8"))
        assert "tenant_id" in payload and "entry_id" in payload and "slug" in payload
        captured["calls"].append((url, headers, payload))
        return True, 200

    import app.services.webhook_service as wh

    monkeypatch.setattr(wh, "get_endpoints_for_tenant", fake_get_endpoints_for_tenant)
    monkeypatch.setattr(wh, "_deliver_with_retries", fake_deliver_with_retries)

    app.dependency_overrides[get_db] = _override_get_db_factory(db_session)
    try:
        tenant = Tenant(slug=f"webhook-{uuid.uuid4().hex[:8]}", name="Webhook Tenant")
        db_session.add(tenant)
        db_session.flush()
        tenant_id = tenant.id

        sec, _ = _mk_section_and_schema(db_session, tenant_id, key="LandingPages")
        headers = auth_headers(
            user_id=7,
            tenant_id=tenant_id,
            permissions=("content:write", "content:publish"),
        )

        r = client.post(
            "/api/v1/content/entries",
            json={
                "tenant_id": tenant_id,
                "section_id": sec.id,
                "schema_version": 1,
                "data": {"slug": "home", "title": "Index"},
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        entry_id = r.json()["id"]

        if event_name == "content.published":
            r2 = client.post(f"/api/v1/content/entries/{entry_id}/publish?tenant_id={tenant_id}", headers=headers)
            assert r2.status_code == 200, r2.text
        elif event_name == "content.unpublished":
            client.post(f"/api/v1/content/entries/{entry_id}/publish?tenant_id={tenant_id}", headers=headers)
            r2 = client.post(f"/api/v1/content/entries/{entry_id}/unpublish?tenant_id={tenant_id}", headers=headers)
            assert r2.status_code == 200, r2.text
        else:
            r2 = client.post(f"/api/v1/content/entries/{entry_id}/archive?tenant_id={tenant_id}", headers=headers)
            assert r2.status_code == 200, r2.text

        assert len(captured["calls"]) == 1
        _, webhook_headers, payload = captured["calls"][0]
        assert webhook_headers["X-Webhook-Event"] == event_name
        assert payload["timestamp"].endswith("Z")
    finally:
        app.dependency_overrides.pop(get_db, None)
