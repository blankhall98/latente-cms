from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.delivery import jiribilla_forms as jf
from app.main import app
from app.models.auth import Tenant
from app.models.content import Entry, Section
from app.models.jiribilla_forms import (
    FORM_TYPE_BOLSA,
    FORM_TYPE_EVENTOS,
    JiribillaFormSubmission,
)
from app.web.admin import router as admin_router

client = TestClient(app)

EVENTOS_URL = "/delivery/v1/jiribilla/eventos-privados"
BOLSA_URL = "/delivery/v1/jiribilla/bolsa-trabajo"


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    jf._rate_store.clear()
    yield
    jf._rate_store.clear()


def _get_or_create_jiribilla(db: Session) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "jiribilla"))
    if tenant is None:
        tenant = Tenant(slug="jiribilla", name="Jiribilla", is_active=True)
        db.add(tenant)
        db.flush()
    tenant.is_active = True
    db.flush()
    return tenant


def _set_settings(db: Session, tenant: Tenant, data: dict) -> None:
    section = db.scalar(
        select(Section).where(Section.tenant_id == tenant.id, Section.key == "settings")
    )
    if section is None:
        section = Section(tenant_id=tenant.id, key="settings", name="Site Settings")
        db.add(section)
        db.flush()
    entry = db.scalar(
        select(Entry).where(
            Entry.tenant_id == tenant.id,
            Entry.section_id == section.id,
            Entry.slug == "settings",
        )
    )
    if entry is None:
        entry = Entry(
            tenant_id=tenant.id,
            section_id=section.id,
            slug="settings",
            schema_version=1,
            status="published",
            data=data,
        )
        db.add(entry)
    else:
        entry.status = "published"
        entry.data = data
    db.flush()


def _eventos_payload(**overrides) -> dict:
    payload = {
        "nombre": "Ana Prueba",
        "correo": "ana@example.com",
        "telefono": "+52 55 1234 5678",
        "tipo_evento": "Empresarial",
        "fecha": "2026-09-15",
        "hora": "19:00",
        "propuesta": "Em",
        "num_personas": 40,
        "descripcion": "Cena corporativa de fin de año.",
    }
    payload.update(overrides)
    return payload


def _bolsa_data(**overrides) -> dict:
    data = {
        "nombre": "Luis Prueba",
        "correo": "luis@example.com",
        "telefono": "+52 55 8765 4321",
        "area_interes": "Cocina",
        "respuesta": "Me interesa el oficio y la cocina de temporada.",
    }
    data.update(overrides)
    return data


def _pdf_file(size_bytes: int = 1024) -> tuple[str, io.BytesIO, str]:
    content = b"%PDF-1.4\n" + b"0" * max(0, size_bytes - 9)
    return ("cv.pdf", io.BytesIO(content), "application/pdf")


class _MailRecorder:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


# ---------------------------------------------------------------------------
# Eventos Privados
# ---------------------------------------------------------------------------

def test_eventos_ok_persists_and_forwards(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio", "eventos_email": "eventos@jiribilla.studio"})
    mail = _MailRecorder()
    monkeypatch.setattr(jf, "send_contact_email", mail)

    r = client.post(EVENTOS_URL, json=_eventos_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    row = db.get(JiribillaFormSubmission, body["id"])
    assert row is not None
    assert row.tenant_id == tenant.id
    assert row.form_type == FORM_TYPE_EVENTOS
    assert row.name == "Ana Prueba"
    assert row.email == "ana@example.com"
    assert row.data["propuesta"] == "Em"
    assert row.data["num_personas"] == 40
    assert row.email_sent is True
    assert row.is_read is False

    assert len(mail.calls) == 1
    assert mail.calls[0]["to_email"] == "eventos@jiribilla.studio"
    assert mail.calls[0]["fields"]["No. de Personas"] == "40"


def test_eventos_falls_back_to_contact_email(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio", "eventos_email": ""})
    mail = _MailRecorder()
    monkeypatch.setattr(jf, "send_contact_email", mail)

    r = client.post(EVENTOS_URL, json=_eventos_payload())
    assert r.status_code == 200, r.text
    assert mail.calls[0]["to_email"] == "hola@jiribilla.studio"


def test_eventos_no_destination_configured_is_503(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "", "eventos_email": ""})
    monkeypatch.setattr(jf, "send_contact_email", _MailRecorder())

    before = db.scalar(
        select(JiribillaFormSubmission.id).order_by(JiribillaFormSubmission.id.desc()).limit(1)
    )
    r = client.post(EVENTOS_URL, json=_eventos_payload())
    assert r.status_code == 503
    after = db.scalar(
        select(JiribillaFormSubmission.id).order_by(JiribillaFormSubmission.id.desc()).limit(1)
    )
    assert before == after


def test_eventos_smtp_failure_still_stores_message(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio"})

    def _boom(**kwargs):
        raise RuntimeError("SMTP down")

    monkeypatch.setattr(jf, "send_contact_email", _boom)

    r = client.post(EVENTOS_URL, json=_eventos_payload())
    assert r.status_code == 200, r.text
    row = db.get(JiribillaFormSubmission, r.json()["id"])
    assert row is not None
    assert row.email_sent is False


def test_eventos_invalid_payload_is_422(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio"})
    monkeypatch.setattr(jf, "send_contact_email", _MailRecorder())

    r = client.post(EVENTOS_URL, json=_eventos_payload(correo="not-an-email", num_personas=0))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Bolsa de Trabajo
# ---------------------------------------------------------------------------

def _patch_storage(monkeypatch, url="https://storage.example.com/jiribilla/cv/test.pdf"):
    uploads: list[str] = []

    def _fake_upload(file_obj, content_type, dest_path):
        uploads.append(dest_path)
        return url

    monkeypatch.setattr(jf, "is_firebase_configured", lambda: True)
    monkeypatch.setattr(jf, "upload_file_to_firebase", _fake_upload)
    return uploads


def test_bolsa_ok_uploads_cv_and_persists(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio", "bolsa_trabajo_email": "rh@jiribilla.studio"})
    mail = _MailRecorder()
    monkeypatch.setattr(jf, "send_contact_email", mail)
    uploads = _patch_storage(monkeypatch)

    r = client.post(BOLSA_URL, data=_bolsa_data(), files={"cv": _pdf_file()})
    assert r.status_code == 200, r.text
    body = r.json()

    row = db.get(JiribillaFormSubmission, body["id"])
    assert row is not None
    assert row.form_type == FORM_TYPE_BOLSA
    assert row.cv_url == "https://storage.example.com/jiribilla/cv/test.pdf"
    assert row.data["area_interes"] == "Cocina"
    assert row.data["cv_filename"] == "cv.pdf"
    assert row.email_sent is True

    assert len(uploads) == 1
    assert uploads[0].startswith("jiribilla/cv/")
    assert mail.calls[0]["to_email"] == "rh@jiribilla.studio"
    assert mail.calls[0]["fields"]["CV"] == row.cv_url


def test_bolsa_rejects_non_pdf(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio"})
    monkeypatch.setattr(jf, "send_contact_email", _MailRecorder())
    _patch_storage(monkeypatch)

    files = {"cv": ("cv.pdf", io.BytesIO(b"GIF89a not a pdf"), "application/pdf")}
    r = client.post(BOLSA_URL, data=_bolsa_data(), files=files)
    assert r.status_code == 415


def test_bolsa_rejects_oversized_cv(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio"})
    monkeypatch.setattr(jf, "send_contact_email", _MailRecorder())
    _patch_storage(monkeypatch)

    r = client.post(
        BOLSA_URL,
        data=_bolsa_data(),
        files={"cv": _pdf_file(size_bytes=jf.MAX_CV_BYTES + 1)},
    )
    assert r.status_code == 413


def test_bolsa_missing_cv_is_422(db: Session, monkeypatch):
    tenant = _get_or_create_jiribilla(db)
    _set_settings(db, tenant, {"contact_email": "hola@jiribilla.studio"})
    monkeypatch.setattr(jf, "send_contact_email", _MailRecorder())
    _patch_storage(monkeypatch)

    r = client.post(BOLSA_URL, data=_bolsa_data())
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Dashboard wiring
# ---------------------------------------------------------------------------

def test_jiribilla_dashboard_order_includes_inboxes():
    order = admin_router._JIRIBILLA_SECTION_DASHBOARD_ORDER
    assert "mensajes_eventos" in order
    assert "mensajes_bolsa" in order
    assert admin_router._section_order_case_for_tenant_slug("jiribilla") is not None


def test_jiribilla_inbox_section_map():
    assert admin_router._JIRIBILLA_INBOX_SECTIONS == {
        "mensajes_eventos": FORM_TYPE_EVENTOS,
        "mensajes_bolsa": FORM_TYPE_BOLSA,
    }
