# app/services/publish_service.py
# ⟶ Reglas de transición + ETag y Cache-Control básicos
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import Response
from sqlalchemy.orm import Session

from app.models.content import Entry

Status = Literal["draft", "published", "archived"]

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
        # opcional: mantener published_at como histórico o limpiarlo; aquí lo mantenemos

    db.flush()
    return entry

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

def apply_cache_headers(response: Response, *, status: Status) -> None:
    if status == "published":
        response.headers["Cache-Control"] = "public, max-age=60"
    else:
        response.headers["Cache-Control"] = "no-store"
