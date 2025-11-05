# app/seeds/content_loader.py
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import select, or_, func

from app.models.auth import Tenant
from app.services.content_service import create_section, add_schema_version, set_active_schema

@dataclass(frozen=True)
class SectionFile:
    section_key: str        # p.ej. "LandingPages"
    section_name: str       # p.ej. "Landing Pages"
    version: int            # p.ej. 1
    file_path: str          # relativo a base_dir: "owa/landing_pages/v1.json"
    is_active: bool = False

def get_tenant_id_by_key_or_name(db: Session, *, tenant_key_or_name: str) -> int:
    key = tenant_key_or_name.strip()
    q = select(Tenant).where(
        or_(func.lower(Tenant.name) == key.lower(), func.lower(Tenant.slug) == key.lower())
    )
    t = db.scalar(q)
    if not t:
        raise RuntimeError(f"Tenant '{tenant_key_or_name}' no encontrado.")
    return int(t.id)

def _read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo de schema: {path.as_posix()}")
    # tolera BOM/UTF-8 y asegura objeto dict
    raw = path.read_bytes()
    txt = raw.decode("utf-8-sig")
    data = json.loads(txt)
    if not isinstance(data, dict):
        raise ValueError(f"El schema en {path.name} debe ser un objeto JSON.")
    return data

def load_section_schema_from_file(
    db: Session,
    *,
    tenant_id: int,
    section_key: str,
    section_name: str,
    version: int,
    file_path: str,
    make_active: bool = False,
) -> Tuple[int, int]:
    """
    Carga UNA versión de schema desde file_path.
    Inserta siempre la versión como inactiva y, si make_active=True, la activa atómicamente.
    Retorna (section_id, version).
    """
    p = pathlib.Path(file_path)
    schema = _read_json(p)

    # (opcional) sanity checks mínimos del schema
    if "type" not in schema and "properties" not in schema:
        # No bloquea, pero avisa: muchos JSON Schema válidos tienen al menos uno
        pass

    with db.begin():  # transacción atómica
        section = create_section(db, tenant_id=tenant_id, key=section_key, name=section_name)
        db.flush()

        add_schema_version(
            db,
            tenant_id=tenant_id,
            section_id=section.id,
            version=version,
            schema=schema,
            title=f"{section_key}@{version}",
            is_active=False,  # clave: insertar inactivo
        )
        if make_active:
            set_active_schema(db, tenant_id=tenant_id, section_id=section.id, version=version)

    return int(section.id), int(version)

def bulk_load_tenant_schemas(
    db: Session,
    *,
    tenant_key_or_name: str,
    base_dir: str,
    files: Iterable[SectionFile],
) -> None:
    tenant_id = get_tenant_id_by_key_or_name(db, tenant_key_or_name=tenant_key_or_name)
    base = pathlib.Path(base_dir)

    # Ordenar por (section_key, version) ayuda cuando hay múltiples versiones
    ordered = sorted(files, key=lambda f: (f.section_key, f.version))

    for f in ordered:
        full = (base / f.file_path).resolve()
        section_id, ver = load_section_schema_from_file(
            db,
            tenant_id=tenant_id,
            section_key=f.section_key,
            section_name=f.section_name,
            version=f.version,
            file_path=full.as_posix(),
            make_active=f.is_active,
        )
        print(f"[seed] {tenant_key_or_name} · {f.section_key}@{ver} (active={f.is_active}) OK (section_id={section_id})")

