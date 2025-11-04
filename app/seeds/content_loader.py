# app/seeds/content_loader.py
# Loader de archivos JSON (por tenant/section/version) -> registra Section + SectionSchema en DB
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session
from sqlalchemy import select, or_, func

from app.models.auth import Tenant  # Paso 7
from app.services.content_service import create_section, add_schema_version, set_active_schema

@dataclass
class SectionFile:
    section_key: str       # p.ej. "LandingPages"
    section_name: str      # p.ej. "Landing Pages"
    version: int           # p.ej. 1
    file_path: str         # ruta relativa a base_dir: "owa/landing_pages/v1.json"
    is_active: bool = False

def get_tenant_id_by_key_or_name(db: Session, *, tenant_key_or_name: str) -> int:
    """
    Busca tenant por name O por slug, case-insensitive.
    Acepta 'OWA' o 'owa'.
    """
    key = tenant_key_or_name.strip()
    q = select(Tenant).where(
        or_(
            func.lower(Tenant.name) == key.lower(),
            func.lower(Tenant.slug) == key.lower(),
        )
    )
    t = db.scalar(q)
    if not t:
        raise RuntimeError(f"Tenant '{tenant_key_or_name}' no encontrado.")
    return t.id

def load_section_schema_from_file(
    db: Session, *,
    tenant_id: int,
    section_key: str,
    section_name: str,
    version: int,
    file_path: str,
    make_active: bool = False,
):
    # 1) crea/obtiene Section
    section = create_section(db, tenant_id=tenant_id, key=section_key, name=section_name)
    db.flush()

    # 2) lee el JSON Schema
    p = pathlib.Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"No existe el archivo de schema: {file_path}")

    schema = json.loads(p.read_text(encoding="utf-8"))

    # 3) registra versión idempotente SIEMPRE como INACTIVA
    add_schema_version(
        db,
        tenant_id=tenant_id,
        section_id=section.id,
        version=version,
        schema=schema,
        title=f"{section_key}@{version}",
        is_active=False,  # ← clave: insert inactive siempre
    )

    # 4) si se marcó como activa, flip atómico (desactiva la previa y activa esta)
    if make_active:
        set_active_schema(db, tenant_id=tenant_id, section_id=section.id, version=version)

    db.commit()

def bulk_load_tenant_schemas(
    db: Session, *,
    tenant_key_or_name: str,
    base_dir: str,
    files: Iterable[SectionFile],
):
    tenant_id = get_tenant_id_by_key_or_name(db, tenant_key_or_name=tenant_key_or_name)
    base = pathlib.Path(base_dir)
    for f in files:
        full = (base / f.file_path).as_posix()
        load_section_schema_from_file(
            db,
            tenant_id=tenant_id,
            section_key=f.section_key,
            section_name=f.section_name,
            version=f.version,
            file_path=full,
            make_active=f.is_active,
        )
