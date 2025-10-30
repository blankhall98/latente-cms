# tests/test_entry_versioning.py
import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.main import app
from app.db.session import get_db


def _auth_headers(uid: int):
    return {"X-User-Id": str(uid)}


@pytest.fixture
def client(db_session):
    # Reutiliza la sesión de pruebas
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _get_section_id(db: Session, tenant_id: int) -> int:
    from app.models.content import Section
    return db.scalar(
        select(Section.id).where(Section.tenant_id == tenant_id, Section.key == "LandingPages")
    )


@pytest.fixture
def tenant_latente_id(db_session):
    # Obtiene el tenant_id del seed (LandingPages)
    from app.models.content import Section
    return db_session.scalar(
        select(Section.tenant_id).where(Section.key == "LandingPages")
    )


def test_versioning_flow(client: TestClient, db_session: Session, tenant_latente_id: int):
    section_id = _get_section_id(db_session, tenant_latente_id)

    # 1) create -> snapshot v1 (reason=create)
    r = client.post(
        "/api/v1/content/entries",
        json={
            "tenant_id": tenant_latente_id,
            "section_id": section_id,
            "schema_version": 1,
            "data": {"slug": "vflow", "title": "VFlow"},
        },
        headers=_auth_headers(1),
    )
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    v = client.get(
        f"/api/v1/content/entries/{entry_id}/versions",
        params={"tenant_id": tenant_latente_id},
        headers=_auth_headers(1),
    )
    assert v.status_code == 200
    versions = v.json()
    assert len(versions) == 1
    assert versions[0]["version_idx"] == 1
    assert versions[0]["reason"] == "create"

    # 2) update -> snapshot v2 (reason=update)
    r2 = client.patch(
        f"/api/v1/content/entries/{entry_id}",
        json={"tenant_id": tenant_latente_id, "data": {"title": "VFlow 2"}},
        headers=_auth_headers(1),
    )
    assert r2.status_code == 200, r2.text

    v2 = client.get(
        f"/api/v1/content/entries/{entry_id}/versions",
        params={"tenant_id": tenant_latente_id},
        headers=_auth_headers(1),
    )
    assert v2.status_code == 200
    versions2 = v2.json()
    assert len(versions2) == 2
    assert versions2[-1]["version_idx"] == 2
    assert versions2[-1]["reason"] == "update"

    # 3) restore v1 -> snapshot v3 (reason=restore)
    rr = client.post(
        f"/api/v1/content/entries/{entry_id}/versions/1/restore",
        json={"tenant_id": tenant_latente_id},
        headers=_auth_headers(9),
    )
    assert rr.status_code == 200, rr.text

    v3 = client.get(
        f"/api/v1/content/entries/{entry_id}/versions",
        params={"tenant_id": tenant_latente_id},
        headers=_auth_headers(1),
    )
    assert v3.status_code == 200
    versions3 = v3.json()
    assert len(versions3) == 3
    assert versions3[-1]["version_idx"] == 3
    assert versions3[-1]["reason"] == "restore"
