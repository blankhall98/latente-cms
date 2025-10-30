#  app/api/delivery/router.py
from __future__ import annotations

import json
from datetime import datetime, date, timezone  # <-- añadimos date
from typing import Iterable, Optional, Dict, Any, List, Set

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Header, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.delivery import DeliveryEntryOut, DeliveryEntryListOut
from app.services.delivery_service import (
    fetch_published_entries,
    fetch_single_published_entry,
)
from app.services.publish_service import (
    parse_httpdate,
    compute_etag_from_bytes,
    apply_delivery_cache_headers,
)

router = APIRouter(prefix="/delivery/v1", tags=["Delivery"])


# --- Helper para serializar datetimes en JSON ---
def _json_default(o):
    """
    Serializa datetime/date a ISO-8601. Para datetime naive, asume UTC.
    """
    if isinstance(o, datetime):
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        else:
            o = o.astimezone(timezone.utc)
        # normalizamos a segundos para estabilidad (sin microsegundos)
        return o.replace(microsecond=0).isoformat()
    if isinstance(o, date):
        return o.isoformat()
    # para tipos no soportados, deja que json lance TypeError
    raise TypeError(f"Type not serializable: {type(o)}")


def _to_utc_seconds(dt: datetime | None) -> datetime | None:
    """
    Normaliza un datetime a UTC y sin microsegundos (precisión de segundos),
    adecuado para comparaciones con If-Modified-Since.
    """
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0)


def _max_last_modified_from_items(items: Iterable[DeliveryEntryOut] | Iterable[Dict[str, Any]]) -> Optional[datetime]:
    """
    Para listados: el Last-Modified será el máximo entre published_at y updated_at
    de los ítems devueltos, normalizado a UTC (segundos).
    """
    last: Optional[datetime] = None
    for it in items:
        # items pueden ser DeliveryEntryOut (obj) o dict; toleramos ambos
        pub = (
            getattr(it, "published_at", None)
            if hasattr(it, "published_at")
            else (it.get("published_at") if isinstance(it, dict) else None)
        )
        upd = (
            getattr(it, "updated_at", None)
            if hasattr(it, "updated_at")
            else (it.get("updated_at") if isinstance(it, dict) else None)
        )

        for cand in (pub, upd):
            cand_utc = _to_utc_seconds(cand)
            if cand_utc and (last is None or cand_utc > last):
                last = cand_utc
    return last


def _parse_fields_param(fields: str | None) -> Set[str] | None:
    """
    Convierte 'a,b,c' -> {'a','b','c'}; ignora vacíos; devuelve None si no hay campos.
    Se aplica únicamente a claves de `data` (no afecta id/slug/etc).
    """
    if not fields:
        return None
    parsed = {f.strip() for f in fields.split(",") if f.strip()}
    return parsed or None


def _parse_data_filters(request: Request) -> Dict[str, str]:
    """
    Convierte query params tipo:
      ?data__category=news&data__lang=es
    en {'category': 'news', 'lang': 'es'}

    Igualdad simple en primer nivel de `data`.
    """
    df: Dict[str, str] = {}
    for k, v in request.query_params.multi_items():
        if not k.startswith("data__"):
            continue
        key = k[len("data__") : ].strip()
        if key:
            df[key] = v
    return df


def _apply_filters_and_projection(
    items: List[DeliveryEntryOut] | List[Dict[str, Any]],
    data_filters: Dict[str, str],
    fields: Set[str] | None,
) -> List[Dict[str, Any]]:
    """
    Aplica filtros de igualdad sobre `data` (nivel 1) y proyecta
    las claves de `data` a las incluidas en `fields` si se especifica.
    Devuelve lista de dicts listos para serializar.
    """
    out: List[Dict[str, Any]] = []

    for it in items:
        # Normaliza a dict
        if hasattr(it, "model_dump"):
            base = it.model_dump(by_alias=True)
        elif isinstance(it, dict):
            base = dict(it)
        else:
            # Fallback para objetos con atributos
            base = {
                "id": getattr(it, "id", None),
                "tenant_id": getattr(it, "tenant_id", None),
                "section_id": getattr(it, "section_id", None),
                "slug": getattr(it, "slug", None),
                "status": getattr(it, "status", None),
                "schema_version": getattr(it, "schema_version", None),
                "data": getattr(it, "data", None),
                "updated_at": getattr(it, "updated_at", None),
                "published_at": getattr(it, "published_at", None),
            }

        data = base.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        # Filtros
        passed = True
        for k, v in data_filters.items():
            val = data.get(k)
            # Comparación de igualdad simple como string
            if val is None or str(val) != v:
                passed = False
                break
        if not passed:
            continue

        # Proyección
        if fields:
            data = {k: data[k] for k in fields if k in data}

        base["data"] = data
        out.append(base)

    return out


@router.get(
    "/entries",
    response_model=DeliveryEntryListOut,
    summary="Listar entries publicados (público)",
)
def list_published_entries(
    request: Request,
    tenant_slug: str = Query(..., description="Slug del tenant"),
    section_key: str | None = Query(None, description="Clave de sección (opcional)"),
    slug: str | None = Query(None, description="Slug exacto (opcional)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    # Paso 19: proyección y filtros
    fields: str | None = Query(
        None,
        description="Lista separada por comas con claves de `data` a devolver (p.ej., fields=title,heroImage)",
    ),
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    if_modified_since: str | None = Header(default=None, alias="If-Modified-Since"),
):
    """
    Lista solo entries en estado 'published'. Aplica:
    - Filtros por claves de `data` (nivel 1): ?data__k=v
    - Proyección de `data`: ?fields=a,b,c   (solo devuelve esas claves dentro de `data`)
    - ETag (If-None-Match → 304, prioridad sobre If-Modified-Since)
    - Last-Modified (If-Modified-Since → 304)
    - Cache-Control específico para listados
    """
    items, total, _etag_legacy = fetch_published_entries(
        db=db,
        tenant_slug=tenant_slug,
        section_key=section_key,
        slug=slug,
        limit=limit,
        offset=offset,
    )

    # Paso 19: filtros y proyección en capa router
    data_filters = _parse_data_filters(request)
    field_set = _parse_fields_param(fields)
    items_projected = _apply_filters_and_projection(items, data_filters, field_set)

    # Importante: si se filtró, el total reportado debería corresponder al conjunto devuelto.
    # Mantenemos `total` original como total bruto, y exponemos items filtrados.
    # Si se desea, se podría ajustar `total` a len(items_projected).
    out = DeliveryEntryListOut(
        total=total,
        limit=limit,
        offset=offset,
        items=items_projected,
    )

    # Serializamos para calcular ETag estable (incluye filtros/proyección)
    out_dict = out.model_dump(by_alias=True, exclude_none=True, mode="json")
    body_bytes = json.dumps(
        out_dict, separators=(",", ":"), ensure_ascii=False, default=_json_default
    ).encode("utf-8")
    etag = compute_etag_from_bytes(body_bytes)

    # Last-Modified (máximo de los ítems), normalizado a UTC (segundos)
    last_modified = _max_last_modified_from_items(items_projected)

    # 1) If-None-Match (ETag) tiene prioridad
    if if_none_match and etag and if_none_match == etag:
        resp = Response(status_code=304)
        apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
        return resp

    # 2) If-Modified-Since (si llegó y tenemos last_modified)
    if if_modified_since and last_modified:
        ims = parse_httpdate(if_modified_since)
        ims = _to_utc_seconds(ims)
        # Si el recurso NO ha cambiado desde ims → 304
        if ims and last_modified <= ims:
            resp = Response(status_code=304)
            apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
            return resp

    # Respuesta 200 con headers de caché avanzados
    resp = Response(content=body_bytes, media_type="application/json")
    apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=False)
    return resp


@router.get(
    "/tenants/{tenant_slug}/sections/{section_key}/entries/{slug}",
    response_model=DeliveryEntryOut,
    summary="Obtener entry publicado (público)",
)
def get_published_entry(
    request: Request,
    tenant_slug: str,
    section_key: str,
    slug: str,
    # Paso 19: proyección
    fields: str | None = Query(
        None,
        description="Lista separada por comas con claves de `data` a devolver (p.ej., fields=title,heroImage)",
    ),
    db: Session = Depends(get_db),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    if_modified_since: str | None = Header(default=None, alias="If-Modified-Since"),
):
    """
    Devuelve un entry publicado. Aplica:
    - Proyección de `data`: ?fields=a,b,c
    - ETag (If-None-Match → 304, prioridad sobre If-Modified-Since)
    - Last-Modified (If-Modified-Since → 304)
    - Cache-Control específico para detalle
    """
    entry = fetch_single_published_entry(db, tenant_slug, section_key, slug)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found or not published")

    # Construir payload (obj -> dict) y proyectar si aplica
    if hasattr(entry, "model_dump"):
        base = entry.model_dump(by_alias=True)
    else:
        base = {
            "id": entry.id,
            "tenant_id": entry.tenant_id,
            "section_id": entry.section_id,
            "slug": entry.slug,
            "status": entry.status,
            "schema_version": entry.schema_version,
            "data": entry.data,
            "updated_at": entry.updated_at,
            "published_at": entry.published_at,
        }

    field_set = _parse_fields_param(fields)
    data = base.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    if field_set:
        data = {k: data[k] for k in field_set if k in data}
    base["data"] = data

    # Serializamos para ETag estable (incluye proyección)
    body_bytes = json.dumps(
        base, separators=(",", ":"), ensure_ascii=False, default=_json_default
    ).encode("utf-8")
    etag = compute_etag_from_bytes(body_bytes)

    # Last-Modified del detalle: published_at (si existe) o updated_at, normalizado a UTC (segundos)
    last_modified: datetime | None = _to_utc_seconds(base.get("published_at") or base.get("updated_at"))

    # 1) If-None-Match (prioridad)
    if if_none_match and etag and if_none_match == etag:
        resp = Response(status_code=304)
        apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=True)
        return resp

    # 2) If-Modified-Since
    if if_modified_since and last_modified:
        ims = parse_httpdate(if_modified_since)
        ims = _to_utc_seconds(ims)
        if ims and last_modified <= ims:
            resp = Response(status_code=304)
            apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=True)
            return resp

    # 200 con headers
    resp = Response(content=body_bytes, media_type="application/json")
    apply_delivery_cache_headers(resp, etag=etag, last_modified=last_modified, is_detail=True)
    return resp
