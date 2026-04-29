from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.main import app
from app.models.auth import Role, Tenant, User, UserTenant
from app.services.passwords import hash_password


def _login_client_user(db: Session, client: TestClient) -> tuple[User, Tenant]:
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"Support Tenant {suffix}", slug=f"support-{suffix}")
    role = Role(key=f"support_role_{suffix}", label="Editor", is_system=False)
    user = User(
        email=f"support-{suffix}@example.com",
        hashed_password=hash_password("secret123"),
        full_name="Support Tester",
        is_active=True,
    )
    db.add_all([tenant, role, user])
    db.flush()
    db.add(UserTenant(user_id=user.id, tenant_id=tenant.id, role_id=role.id))
    db.flush()

    response = client.post(
        "/login",
        data={"email": user.email, "password": "secret123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return user, tenant


def test_admin_support_requires_login():
    with TestClient(app) as client:
        response = client.get("/admin/support", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/login")


def test_admin_support_page_renders_for_authenticated_user(db: Session):
    with TestClient(app) as client:
        _, tenant = _login_client_user(db, client)
        response = client.get("/admin/support")

    assert response.status_code == 200
    assert "How To Use The Dashboard" in response.text
    assert "Request Support" in response.text
    assert tenant.name in response.text


def test_admin_support_submit_sends_email(db: Session, monkeypatch):
    import app.web.admin.router as admin_router_module

    sent: dict = {}

    def fake_send_contact_email(**kwargs):
        sent.update(kwargs)

    monkeypatch.setattr(admin_router_module, "send_contact_email", fake_send_contact_email)

    with TestClient(app) as client:
        user, tenant = _login_client_user(db, client)
        response = client.post(
            "/admin/support",
            data={
                "name": "Client User",
                "sender_email": "client@example.com",
                "topic": "publishing",
                "priority": "high",
                "message": "Publishing failed after saving the page.",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/support?sent=1"
    assert sent["to_email"] == settings.SUPPORT_EMAIL
    assert sent["sender_name"] == "Client User"
    assert sent["sender_email"] == "client@example.com"
    assert sent["tenant_name"] == "Latente CMS Support"
    assert "High: Publishing content" in sent["subject"]
    assert sent["fields"]["Logged-in user"] == user.email
    assert sent["fields"]["Project"] == f"{tenant.name} /{tenant.slug}"
