import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.main import app
from app.db.session import get_db
from app.core.config import settings

def _auth(uid: int):
    return {"X-User-Id": str(uid)}

@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()

def _section_id(db: Session, tenant_id: int):
    from app.models.content import Section
    return db.scalar(select(Section.id).where(Section.tenant_id == tenant_id, Section.key == "LandingPages"))

@pytest.fixture
def tenant_latente_id(db_session):
    from app.models.content import Section
    return db_session.scalar(select(Section.tenant_id).where(Section.key == "LandingPages"))

def test_payload_cap(client: TestClient, db_session: Session, monkeypatch, tenant_latente_id: int):
    # Activar cap bajo para test
    monkeypatch.setattr(settings, "MAX_ENTRY_DATA_KB", 1)
    sid = _section_id(db_session, tenant_latente_id)
    big_text = "x" * 4096  # ~4KB
    r = client.post("/api/v1/content/entries", json={
        "tenant_id": tenant_latente_id,
        "section_id": sid,
        "schema_version": 1,
        "data": {"slug": "big", "title": big_text}
    }, headers=_auth(1))
    assert r.status_code == 413
    assert "Payload too large" in r.text

def test_idempotency_create(client: TestClient, db_session: Session, tenant_latente_id: int, monkeypatch):
    monkeypatch.setattr(settings, "IDEMPOTENCY_ENABLED", True)
    sid = _section_id(db_session, tenant_latente_id)
    key = "test-key-123"

    r1 = client.post("/api/v1/content/entries", json={
        "tenant_id": tenant_latente_id,
        "section_id": sid,
        "schema_version": 1,
        "data": {"slug": "idem", "title": "A"}
    }, headers={**_auth(2), "Idempotency-Key": key})
    assert r1.status_code == 201
    e1 = r1.json()["id"]

    r2 = client.post("/api/v1/content/entries", json={
        "tenant_id": tenant_latente_id,
        "section_id": sid,
        "schema_version": 1,
        "data": {"slug": "idem", "title": "A"}
    }, headers={**_auth(2), "Idempotency-Key": key})
    assert r2.status_code == r1.status_code
    assert r2.json()["id"] == e1
    assert r2.headers.get("Idempotent-Replay") == "true"

def test_rate_limit_writes(client: TestClient, db_session: Session, tenant_latente_id: int, monkeypatch):
    # Activar RL y bajar a 3 req/min para el test
    monkeypatch.setattr(settings, "RATELIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATELIMIT_WRITE_PER_MIN", 3)

    sid = _section_id(db_session, tenant_latente_id)
    for i in range(3):
        r = client.post("/api/v1/content/entries", json={
            "tenant_id": tenant_latente_id,
            "section_id": sid,
            "schema_version": 1,
            "data": {"slug": f"rl{i}", "title": "ok"}
        }, headers=_auth(7))
        assert r.status_code == 201, r.text

    r4 = client.post("/api/v1/content/entries", json={
        "tenant_id": tenant_latente_id,
        "section_id": sid,
        "schema_version": 1,
        "data": {"slug": "rl3", "title": "blocked"}
    }, headers=_auth(7))
    assert r4.status_code == 429
    assert "Rate limit exceeded" in r4.text
