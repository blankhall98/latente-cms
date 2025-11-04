from __future__ import annotations
from typing import Tuple, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, desc

from app.models.content import Entry, Section
from app.models.auth import Tenant  # ajusta si tu Tenant vive en otro módulo
from app.schemas.delivery import DeliveryEntryOut

# Opcional: si tu proyecto ya tiene versionado (Paso 17)
try:
    from app.models.content import EntryVersion
    HAS_ENTRY_VERSION = True
except Exception:  # pragma: no cover
    EntryVersion = None  # type: ignore
    HAS_ENTRY_VERSION = False


def _base_published_query():
    """
    Entries con status='published'. Útil para listados.
    (Para detalle, usamos _effective_published_payload)
    """
    return (
        select(Entry)
        .join(Section, Section.id == Entry.section_id)
        .join(Tenant, Tenant.id == Entry.tenant_id)
        .where(Entry.status == "published")
    )


def _latest_published_snapshot(db: Session, entry_id: int) -> Optional[dict]:
    """
    Intenta recuperar el último snapshot 'publicado' para un entry dado.
    Asume que en tu modelo EntryVersion guardas `data` del entry al momento del publish.
    Se intenta filtrar por 'reason' si existe; si no, se toma el más reciente.
    """
    if not HAS_ENTRY_VERSION:
        return None

    # Primero intentamos por razón = publish (si la columna existe)
    q = select(EntryVersion).where(EntryVersion.entry_id == entry_id)

    # Filtrado defensivo por 'reason' si la columna existe:
    # Nota: cambia 'reason' por el nombre real en tu modelo (p.ej. 'action')
    try:
        q_pub = q.where(EntryVersion.reason.in_(["publish", "PUBLISH", "published"]))
        row = db.scalars(q_pub.order_by(desc(getattr(EntryVersion, "created_at", EntryVersion.id)))).first()
        if row and getattr(row, "data", None):
            return dict(row.data)
    except Exception:
        pass

    # Caída general: último snapshot por created_at (o id)
    row = db.scalars(q.order_by(desc(getattr(EntryVersion, "created_at", EntryVersion.id)))).first()
    if row and getattr(row, "data", None):
        return dict(row.data)
    return None


def _effective_published_payload(db: Session, entry: Entry) -> Optional[dict]:
    """
    Regresa el payload 'publicado' efectivo de un entry:
    - Si el entry está en 'published' y tiene data, usa esa data.
    - Si NO, intenta el último snapshot publicado (o último snapshot disponible).
    - Si no hay nada, devuelve None.
    """
    status_val = getattr(entry, "status", None)
    data_val = getattr(entry, "data", None)

    # Caso 1: la fila está publicada y tiene data
    if (status_val == "published") and isinstance(data_val, dict) and data_val:
        return data_val

    # Caso 2: intentar snapshot
    snap = _latest_published_snapshot(db, int(entry.id))
    if isinstance(snap, dict) and snap:
        return snap

    # Sin data efectiva publicada
    return None


def fetch_published_entries(
    db: Session,
    tenant_slug: str,
    section_key: str | None,
    slug: str | None,
    limit: int,
    offset: int,
) -> Tuple[List[DeliveryEntryOut], int, str | None]:
    """
    Listado público. Por simplicidad conservamos el criterio base: status='published'.
    (Si más adelante quieres listar también entradas actualmente en draft pero con snapshot publicado,
     podemos añadir una ruta alternativa para 'published_effective_list'.)
    """
    q = _base_published_query().where(Tenant.slug == tenant_slug)
    cnt = _base_published_query().where(Tenant.slug == tenant_slug)

    if section_key:
        q = q.where(Section.key == section_key)
        cnt = cnt.where(Section.key == section_key)
    if slug:
        q = q.where(Entry.slug == slug)
        cnt = cnt.where(Entry.slug == slug)

    total = db.scalar(select(func.count()).select_from(cnt.subquery())) or 0

    q = (
        q.order_by(
            Entry.published_at.desc().nullslast(),
            Entry.updated_at.desc().nullslast(),
            Entry.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = db.scalars(q).all()

    items: List[DeliveryEntryOut] = []
    for e in rows:
        items.append(
            DeliveryEntryOut(
                id=e.id,
                tenant_id=e.tenant_id,
                section_id=e.section_id,
                slug=e.slug,
                status=e.status,
                schema_version=e.schema_version,
                data=e.data or {},  # en lista mantenemos criterio simple
                updated_at=e.updated_at,
                published_at=getattr(e, "published_at", None),
            )
        )

    # ETag simple de lista: hash de (tenant|section|slug|total|max_ts)
    etag = None
    try:
        import hashlib
        max_updated = max((i.updated_at for i in items if i.updated_at), default=None)
        max_published = max((i.published_at for i in items if i.published_at), default=None)
        key = f"{tenant_slug}|{section_key or ''}|{slug or ''}|{total}|{max_updated or ''}|{max_published or ''}"
        etag = hashlib.sha256(key.encode("utf-8")).hexdigest()
    except Exception:
        etag = None

    return items, int(total), etag


def fetch_single_published_entry(
    db: Session,
    tenant_slug: str,
    section_key: str,
    slug: str,
) -> Optional[DeliveryEntryOut]:
    """
    Detalle público "efectivo": devuelve SIEMPRE la última versión publicada disponible.
    - Si la fila está en published → usa su data.
    - Si la fila está en draft → usa el último snapshot publicado (o el más reciente).
    - Si no existe data publicada → None.
    """
    # Primero localizamos la fila por claves (sin depender del status)
    row = db.execute(
        select(Entry, Section, Tenant)
        .join(Section, Section.id == Entry.section_id)
        .join(Tenant, Tenant.id == Entry.tenant_id)
        .where(
            and_(
                Tenant.slug == tenant_slug,
                Section.key == section_key,
                Entry.slug == slug,
            )
        )
        .limit(1)
    ).first()

    if not row:
        return None

    entry, section, tenant = row
    effective = _effective_published_payload(db, entry)
    if not isinstance(effective, dict) or not effective:
        return None

    return DeliveryEntryOut(
        id=entry.id,
        tenant_id=entry.tenant_id,
        section_id=entry.section_id,
        slug=entry.slug,
        status="published",  # porque devolvemos la vista publicada efectiva
        schema_version=entry.schema_version,
        data=effective,
        updated_at=entry.updated_at,
        published_at=getattr(entry, "published_at", None),
    )

