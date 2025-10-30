# app/services/publish_service.py
# ⟶ Reglas de transición + ETag y Cache-Control + helpers HTTP-date/ETag
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from email.utils import format_datetime, parsedate_to_datetime
from fastapi import Response
from sqlalchemy.orm import Session

from app.models.content import Entry

Status = Literal["draft", "published", "archived"]


# -----------------------------
# Transiciones de estado Entry
# -----------------------------
def can_transition(src: Status, dst: Status) -> bool:
    if src == dst:
        return True
    if src == "draft" and dst in ("published", "archived"):
        return True
    if src == "published" and dst in ("draft", "archived"):
        return True
    if src == "archived" and dst in ("draft",):  # volver a borrador antes de publicar
        return True
    return False


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def transition_entry_status(db: Session, entry: Entry, dst: Status) -> Entry:
    src = entry.status
    if not can_transition(src, dst):
        raise ValueError(f"Invalid transition {src} → {dst}")

    entry.status = dst
    if dst == "published":
        entry.published_at = _now_utc()
        entry.archived_at = None
    elif dst == "draft":
        # “unpublish”: limpiamos published_at; conservamos archived_at si existe
        entry.published_at = None
    elif dst == "archived":
        entry.archived_at = _now_utc()
        # mantenemos published_at como histórico

    db.flush()
    return entry


# -----------------------------
# ETags y Cache-Control básicos
# -----------------------------
def compute_etag(entry: Entry) -> str:
    payload = {
        "id": entry.id,
        "slug": entry.slug,
        "schema_version": entry.schema_version,
        "status": entry.status,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        "data": entry.data,  # json estable
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_etag_from_bytes(body: bytes) -> str:
    """
    ETag como sha256 hex del cuerpo bytes.
    """
    return hashlib.sha256(body).hexdigest()


def apply_cache_headers(response: Response, *, status: Status) -> None:
    if status == "published":
        response.headers["Cache-Control"] = "public, max-age=60"
    else:
        response.headers["Cache-Control"] = "no-store"


# -----------------------------
# HTTP-date helpers (UTC)
# -----------------------------
def _to_utc(dt: datetime) -> datetime:
    """
    Asegura que el datetime sea timezone-aware en UTC.
    - Si viene naive, se asume UTC (no desplaza).
    - Si viene con tz, se convierte a UTC con astimezone.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def httpdate(dt: datetime) -> str:
    """
    Convierte un datetime a HTTP-date (RFC 7231).
    format_datetime(..., usegmt=True) exige tz==UTC.
    """
    return format_datetime(_to_utc(dt), usegmt=True)


def parse_httpdate(value: str) -> datetime | None:
    """
    Parsea un HTTP-date a datetime aware (UTC). Devuelve None si falla.
    """
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


# -----------------------------
# Políticas de caché Delivery
# -----------------------------
def cache_policy_for_list() -> dict[str, str]:
    """
    Política de caché para listados Delivery.
    """
    return {
        "Cache-Control": "public, max-age=60, stale-while-revalidate=120",
    }


def cache_policy_for_detail() -> dict[str, str]:
    """
    Política de caché para detalle Delivery.
    """
    return {
        "Cache-Control": "public, max-age=300, stale-while-revalidate=600",
    }


def apply_delivery_cache_headers(
    resp: Response,
    *,
    etag: str | None,
    last_modified: datetime | None,
    is_detail: bool,
) -> None:
    """
    Aplica ETag, Last-Modified y Cache-Control (lista vs detalle).
    """
    if etag:
        resp.headers["ETag"] = etag
    if last_modified:
        resp.headers["Last-Modified"] = httpdate(last_modified)

    policy = cache_policy_for_detail() if is_detail else cache_policy_for_list()
    for k, v in policy.items():
        resp.headers[k] = v

