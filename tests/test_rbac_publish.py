# tests/test_rbac_publish.py
# ⟶ Role exige label NOT NULL; pásalo y también override get_db

from __future__ import annotations
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import SessionLocal
from app.db.session import get_db as original_get_db
from app.models.auth import Tenant, User, Role, Permission, UserTenant, RolePermission
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

def _mk_user(db: Session) -> User:
    u = User(email=f"u-{uuid.uuid4().hex[:6]}@test.com", hashed_password="x")
    db.add(u); db.flush()
    return u

def _mk_tenant(db: Session) -> Tenant:
    t = Tenant(name=f"T-{uuid.uuid4().hex[:6]}", slug=f"t-{uuid.uuid4().hex[:6]}")
    db.add(t); db.flush()
    return t

def _mk_role(db: Session, key="author", label="Author") -> Role:
    existing = db.query(Role).filter_by(key=key).first()
    if existing:
        return existing
    r = Role(key=key, label=label, scope="core", is_system=False)
    db.add(r); db.flush()
    return r

def _perm(db: Session, key: str) -> Permission:
    p = db.query(Permission).filter_by(key=key).first()
    if p:
        return p
    p = Permission(key=key, description=key)
    db.add(p); db.flush()
    return p

def _attach(db: Session, user: User, tenant: Tenant, role: Role):
    ut = UserTenant(user_id=user.id, tenant_id=tenant.id, role_id=role.id)
    db.add(ut); db.flush()
    return ut

def _grant(db: Session, role: Role, perm: Permission):
    rp = RolePermission(role_id=role.id, permission_id=perm.id)
    db.add(rp); db.flush()
    return rp

def _mk_entry(db: Session, tenant_id: int, section_id: int):
    payload = EntryCreate(
        tenant_id=tenant_id, section_id=section_id,
        slug="home", schema_version=1, status="draft",
        data={"hero": {"title": "Hola"}}
    )
    e = create_entry(db, payload); db.flush()
    return e

def test_publish_requires_permission(db: Session):
    # Usa la MISMA sesión en el endpoint
    def _override_get_db():
        yield db
    app.dependency_overrides[original_get_db] = _override_get_db

    user = _mk_user(db)
    tenant = _mk_tenant(db)
    role = _mk_role(db, "author", "Author")
    _attach(db, user, tenant, role)

    section = create_section(db, tenant_id=tenant.id, key="LandingPages", name="Landing Pages")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"hero": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
        "required": ["hero"]
    }
    add_schema_version(db, tenant_id=tenant.id, section_id=section.id, version=1, schema=schema, title="v1", is_active=True)
    entry = _mk_entry(db, tenant.id, section.id)
    db.commit()

    headers = {"X-User-Id": str(user.id)}

    # sin permiso → 403
    r = client.post(f"/api/v1/content/entries/{entry.id}/publish?tenant_id={tenant.id}", headers=headers)
    assert r.status_code == 403

    # otorgar permiso y reintentar → 200
    p_publish = _perm(db, "content:publish")
    _grant(db, role, p_publish)
    db.commit()

    r2 = client.post(f"/api/v1/content/entries/{entry.id}/publish?tenant_id={tenant.id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == "published"

    # Limpia override
    app.dependency_overrides.pop(original_get_db, None)


