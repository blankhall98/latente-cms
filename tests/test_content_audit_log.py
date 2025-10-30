import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, func

from app.main import app
from app.db.session import get_db
from app.models.audit import ContentAuditLog, ContentAction
from app.models.content import Entry, Section
from app.api.deps import auth as auth_deps
from app.models.auth import Tenant


@pytest.fixture
def client(db_session: Session, monkeypatch):
    # Compartir la misma sesión en los endpoints durante el test
    def _get_db_override():
        yield db_session
    app.dependency_overrides[get_db] = _get_db_override

    # Por defecto, negar permisos; se habilitan por test según sea necesario
    monkeypatch.setattr("app.api.deps.auth.user_has_permission", lambda *args, **kwargs: False)

    return TestClient(app)


@pytest.fixture
def tenant_latente_id(db_session: Session) -> int:
    q = db_session.execute(
        select(Tenant.id).where(
            or_(
                func.lower(Tenant.slug) == "latente",
                func.lower(Tenant.name) == "latente",
            )
        )
    ).scalar()
    return int(q) if q else 3  # fallback documentado


def _auth_headers(user_id: int | None = 1):
    headers = {}
    if user_id is not None:
        headers["X-User-Id"] = str(user_id)
    return headers


def _get_section_id(db: Session, tenant_id: int, key: str = "LandingPages") -> int:
    sid = db.execute(
        select(Section.id).where(Section.tenant_id == tenant_id, Section.key == key)
    ).scalar()
    assert sid is not None, f"Section '{key}' not found for tenant {tenant_id}. Seed requerido."
    return int(sid)


def test_audit_on_create_entry(client: TestClient, db_session: Session, tenant_latente_id: int):
    section_id = _get_section_id(db_session, tenant_latente_id)
    payload = {
        "tenant_id": tenant_latente_id,
        "section_id": section_id,
        "schema_version": 1,
        "data": {"slug": "home", "title": "Home"}
    }
    r = client.post("/api/v1/content/entries", json=payload, headers=_auth_headers(123))
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    logs = db_session.query(ContentAuditLog).filter_by(entry_id=entry_id).all()
    assert len(logs) >= 1
    log = logs[0]
    assert log.action == ContentAction.CREATE
    assert log.user_id == 123
    assert log.details.get("slug") in ("home", None)


def test_audit_on_update_entry(client: TestClient, db_session: Session, tenant_latente_id: int):
    section_id = _get_section_id(db_session, tenant_latente_id)
    r = client.post("/api/v1/content/entries", json={
        "tenant_id": tenant_latente_id,
        "section_id": section_id,
        "schema_version": 1,
        "data": {"slug": "about", "title": "About"}
    }, headers=_auth_headers(9))
    assert r.status_code == 201
    entry_id = r.json()["id"]

    r2 = client.patch(f"/api/v1/content/entries/{entry_id}", json={
        "tenant_id": tenant_latente_id,
        "data": {"title": "About us"}
    }, headers=_auth_headers(9))
    assert r2.status_code == 200

    logs = (db_session.query(ContentAuditLog)
            .filter_by(entry_id=entry_id)
            .order_by(ContentAuditLog.created_at.asc())
            .all())
    assert len(logs) >= 2
    last = logs[-1]
    assert last.action == ContentAction.UPDATE
    assert "changed_keys" in last.details
    assert "title" in last.details["changed_keys"]


def test_audit_on_publish(client: TestClient, db_session: Session, tenant_latente_id: int, monkeypatch):
    # habilitar permiso de publicación
    monkeypatch.setattr("app.api.deps.auth.user_has_permission", lambda *args, **kwargs: True)
    section_id = _get_section_id(db_session, tenant_latente_id)

    r = client.post("/api/v1/content/entries", json={
        "tenant_id": tenant_latente_id,
        "section_id": section_id,
        "schema_version": 1,
        "data": {"slug": "news"}
    }, headers=_auth_headers(42))
    assert r.status_code == 201
    entry_id = r.json()["id"]

    rp = client.post(
        f"/api/v1/content/entries/{entry_id}/publish",
        json={"tenant_id": tenant_latente_id},
        headers=_auth_headers(42)
    )
    assert rp.status_code == 200

    logs = db_session.query(ContentAuditLog).filter_by(entry_id=entry_id, action=ContentAction.PUBLISH).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.user_id == 42
    assert log.details.get("after_status") == "published"

