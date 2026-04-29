from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models.auth import Permission, Role, RolePermission, Tenant, User, UserTenant
from app.schemas.content import EntryCreate
from app.services.content_service import add_schema_version, create_entry, create_section


client = TestClient(app)


def _mk_user(db: Session) -> User:
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@test.com", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    return user


def _mk_tenant(db: Session) -> Tenant:
    tenant = Tenant(name=f"T-{uuid.uuid4().hex[:8]}", slug=f"t-{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _mk_role(db: Session, base_key: str = "author", label: str = "Author") -> Role:
    role = Role(key=f"{base_key}_{uuid.uuid4().hex[:8]}", label=label, is_system=False)
    db.add(role)
    db.flush()
    return role


def _perm(db: Session, key: str) -> Permission:
    perm = db.query(Permission).filter_by(key=key).first()
    if perm:
        return perm
    perm = Permission(key=key, description=key)
    db.add(perm)
    db.flush()
    return perm


def _attach(db: Session, user: User, tenant: Tenant, role: Role) -> UserTenant:
    user_tenant = UserTenant(user_id=user.id, tenant_id=tenant.id, role_id=role.id)
    db.add(user_tenant)
    db.flush()
    return user_tenant


def _grant(db: Session, role: Role, perm: Permission) -> None:
    exists = db.scalar(
        select(RolePermission.id)
        .where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == perm.id,
        )
        .limit(1)
    )
    if not exists:
        db.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.flush()


def _mk_entry(db: Session, tenant_id: int, section_id: int):
    payload = EntryCreate(
        tenant_id=tenant_id,
        section_id=section_id,
        slug="home",
        schema_version=1,
        status="draft",
        data={"hero": {"title": "Hola"}},
    )
    entry = create_entry(db, payload)
    db.flush()
    return entry


def test_publish_requires_permission(db: Session, auth_headers):
    user = _mk_user(db)
    tenant = _mk_tenant(db)
    role = _mk_role(db, "author", "Author")
    _attach(db, user, tenant, role)

    section = create_section(db, tenant_id=tenant.id, key="LandingPages", name="Landing Pages")
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
        tenant_id=tenant.id,
        section_id=section.id,
        version=1,
        schema=schema,
        title="v1",
        is_active=True,
    )
    entry = _mk_entry(db, tenant.id, section.id)
    headers = auth_headers(user_id=user.id)

    r = client.post(f"/api/v1/content/entries/{entry.id}/publish?tenant_id={tenant.id}", headers=headers)
    assert r.status_code == 403

    p_publish = _perm(db, "content:publish")
    _grant(db, role, p_publish)

    r2 = client.post(f"/api/v1/content/entries/{entry.id}/publish?tenant_id={tenant.id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == "published"
