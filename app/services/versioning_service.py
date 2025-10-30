# app/services/versioning_service.py
from __future__ import annotations

from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.content import Entry, EntryVersion


def _next_version_idx(db: Session, tenant_id: int, entry_id: int) -> int:
    """
    Calcula el siguiente índice de versión (version_idx) para un Entry dado.
    """
    max_idx = db.scalar(
        select(func.max(EntryVersion.version_idx)).where(
            EntryVersion.tenant_id == tenant_id,
            EntryVersion.entry_id == entry_id,
        )
    )
    return 1 if max_idx is None else int(max_idx) + 1


def create_entry_snapshot(
    db: Session,
    *,
    entry: Entry,
    reason: str,
    created_by: Optional[int] = None,
) -> EntryVersion:
    """
    Crea un snapshot de la versión actual del Entry.
    - No hace commit; el caller debe hacer db.commit().
    - Incluye section_id para facilitar auditorías/consultas.
    """
    snap = EntryVersion(
        tenant_id=entry.tenant_id,
        entry_id=entry.id,
        section_id=entry.section_id,
        version_idx=_next_version_idx(db, entry.tenant_id, entry.id),
        schema_version=entry.schema_version,
        status=entry.status,
        data=dict(entry.data or {}),
        reason=reason,
        created_by=created_by,
    )
    db.add(snap)
    return snap


# Alias compatible con el import utilizado en los endpoints
def create_snapshot_for_entry(
    db: Session,
    *,
    entry: Entry,
    reason: str,
    created_by: Optional[int] = None,
) -> EntryVersion:
    return create_entry_snapshot(db, entry=entry, reason=reason, created_by=created_by)


def list_versions_for_entry(
    db: Session,
    *,
    tenant_id: int,
    entry_id: int,
) -> List[EntryVersion]:
    """
    Lista los snapshots (versiones) de un Entry en orden ascendente por version_idx.
    """
    return list(
        db.scalars(
            select(EntryVersion)
            .where(
                EntryVersion.tenant_id == tenant_id,
                EntryVersion.entry_id == entry_id,
            )
            .order_by(EntryVersion.version_idx.asc())
        )
    )

