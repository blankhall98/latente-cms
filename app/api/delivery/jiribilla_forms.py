from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import get_db
from app.models.auth import Tenant
from app.models.content import Entry, Section
from app.models.jiribilla_forms import (
    FORM_TYPE_BOLSA,
    FORM_TYPE_EVENTOS,
    JiribillaFormSubmission,
)
from app.services.firebase_storage import is_firebase_configured, upload_file_to_firebase
from app.services.mail_service import send_contact_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/delivery/v1/jiribilla", tags=["Jiribilla Forms"])

TENANT_SLUG = "jiribilla"
MAX_CV_BYTES = 25 * 1024 * 1024  # 25 MB, matches the site's stated limit

# ---------------------------------------------------------------------------
# In-memory IP rate limiter (same policy as the generic contact endpoint)
# ---------------------------------------------------------------------------
_rate_store: dict[str, list[float]] = defaultdict(list)
_WINDOW_SECONDS = 60.0


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    recent = [t for t in _rate_store[ip] if t > cutoff]
    if len(recent) >= settings.CONTACT_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment before trying again.",
        )
    recent.append(now)
    _rate_store[ip] = recent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_jiribilla_tenant(db: Session) -> Tenant:
    tenant = db.scalar(
        select(Tenant).where(Tenant.slug == TENANT_SLUG, Tenant.is_active.is_(True))
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


def _get_settings_data(db: Session, tenant: Tenant) -> dict:
    entry = db.scalar(
        select(Entry)
        .join(Section, Section.id == Entry.section_id)
        .where(
            Entry.tenant_id == tenant.id,
            Section.key == "settings",
            Entry.status == "published",
        )
        .order_by(Entry.updated_at.desc())
        .limit(1)
    )
    if entry and isinstance(entry.data, dict):
        return entry.data
    return {}


def _resolve_destination(db: Session, tenant: Tenant, form_email_key: str) -> str:
    data = _get_settings_data(db, tenant)
    destination = (data.get(form_email_key) or "").strip() or (data.get("contact_email") or "").strip()
    if not destination:
        raise HTTPException(
            status_code=503,
            detail="This form is not configured for this site.",
        )
    return destination


def _forward_email(
    *,
    to_email: str,
    sender_name: str,
    sender_email: str,
    subject: str,
    fields: dict[str, str],
    tenant_name: str,
    form_label: str,
) -> bool:
    try:
        send_contact_email(
            to_email=to_email,
            sender_name=sender_name,
            sender_email=sender_email,
            subject=subject,
            fields=fields,
            tenant_name=tenant_name,
        )
        return True
    except Exception as exc:
        # The submission is already stored — never fail the request over SMTP.
        logger.error(
            "jiribilla %s: email forward failed → %s: %s",
            form_label, to_email, exc, exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Eventos Privados
# ---------------------------------------------------------------------------

class EventosPrivadosIn(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=160)
    correo: EmailStr
    telefono: str = Field(..., min_length=1, max_length=64)
    tipo_evento: str = Field(..., min_length=1, max_length=80)
    fecha: date
    hora: str = Field(..., min_length=1, max_length=32)
    propuesta: str = Field(..., min_length=1, max_length=80)
    num_personas: int = Field(..., ge=1, le=100000)
    descripcion: str = Field("", max_length=5000)


@router.post("/eventos-privados", summary="Submit the Jiribilla private-events form (public)")
def submit_eventos_privados(
    payload: EventosPrivadosIn,
    request: Request,
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    tenant = _get_jiribilla_tenant(db)
    destination = _resolve_destination(db, tenant, "eventos_email")

    extra = {
        "tipo_evento": payload.tipo_evento.strip(),
        "fecha": payload.fecha.isoformat(),
        "hora": payload.hora.strip(),
        "propuesta": payload.propuesta.strip(),
        "num_personas": payload.num_personas,
        "descripcion": payload.descripcion.strip(),
    }
    submission = JiribillaFormSubmission(
        tenant_id=int(tenant.id),
        form_type=FORM_TYPE_EVENTOS,
        name=payload.nombre.strip(),
        email=str(payload.correo).strip().lower(),
        phone=payload.telefono.strip(),
        data=extra,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    sent = _forward_email(
        to_email=destination,
        sender_name=submission.name,
        sender_email=submission.email,
        subject=f"Nueva solicitud de evento privado — {submission.name}",
        fields={
            "Nombre": submission.name,
            "Correo": submission.email,
            "Teléfono": submission.phone,
            "Tipo de Evento": extra["tipo_evento"],
            "Fecha del evento": extra["fecha"],
            "Hora": extra["hora"],
            "Propuesta": extra["propuesta"],
            "No. de Personas": str(extra["num_personas"]),
            "Descripción": extra["descripcion"],
        },
        tenant_name=tenant.name,
        form_label="eventos-privados",
    )
    if sent:
        submission.email_sent = True
        db.commit()

    return {"ok": True, "id": int(submission.id)}


# ---------------------------------------------------------------------------
# Bolsa de Trabajo
# ---------------------------------------------------------------------------

def _validate_cv(cv: UploadFile) -> int:
    if not cv or not cv.filename:
        raise HTTPException(status_code=422, detail="CV file is required.")

    cv.file.seek(0, 2)
    size = cv.file.tell()
    cv.file.seek(0)
    if size > MAX_CV_BYTES:
        raise HTTPException(status_code=413, detail="CV must be 25 MB or less.")
    if size == 0:
        raise HTTPException(status_code=422, detail="CV file is empty.")

    head = cv.file.read(5)
    cv.file.seek(0)
    if not head.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="CV must be a PDF file.")
    return size


@router.post("/bolsa-trabajo", summary="Submit the Jiribilla job-application form (public)")
def submit_bolsa_trabajo(
    request: Request,
    nombre: str = Form(..., min_length=1, max_length=160),
    correo: EmailStr = Form(...),
    telefono: str = Form(..., min_length=1, max_length=64),
    area_interes: str = Form(..., min_length=1, max_length=80),
    respuesta: str = Form("", max_length=5000),
    cv: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    tenant = _get_jiribilla_tenant(db)
    destination = _resolve_destination(db, tenant, "bolsa_trabajo_email")

    _validate_cv(cv)
    if not is_firebase_configured():
        raise HTTPException(status_code=503, detail="File uploads are not configured.")

    dest_path = f"{TENANT_SLUG}/cv/{uuid.uuid4().hex}.pdf"
    try:
        cv_url = upload_file_to_firebase(cv.file, "application/pdf", dest_path)
    except Exception as exc:
        logger.error("jiribilla bolsa-trabajo: CV upload failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Could not store the CV. Please try again later.",
        ) from exc

    extra = {
        "area_interes": area_interes.strip(),
        "respuesta": respuesta.strip(),
        "cv_filename": cv.filename,
    }
    submission = JiribillaFormSubmission(
        tenant_id=int(tenant.id),
        form_type=FORM_TYPE_BOLSA,
        name=nombre.strip(),
        email=str(correo).strip().lower(),
        phone=telefono.strip(),
        data=extra,
        cv_url=cv_url,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    sent = _forward_email(
        to_email=destination,
        sender_name=submission.name,
        sender_email=submission.email,
        subject=f"Nueva postulación ({extra['area_interes']}) — {submission.name}",
        fields={
            "Nombre": submission.name,
            "Correo": submission.email,
            "Teléfono": submission.phone,
            "Área de interés": extra["area_interes"],
            "Respuesta": extra["respuesta"],
            "CV": cv_url,
        },
        tenant_name=tenant.name,
        form_label="bolsa-trabajo",
    )
    if sent:
        submission.email_sent = True
        db.commit()

    return {"ok": True, "id": int(submission.id)}
