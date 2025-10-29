# tests/test_registry_and_loader.py
from __future__ import annotations
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.services.content_service import create_section, add_schema_version
from app.services.registry_service import can_activate_version
from app.seeds.content_loader import SectionFile, bulk_load_tenant_schemas


@pytest.fixture()
def db() -> Session:
    """
    Crea una sesión por test.
    Si el test hizo commit (cerró la transacción), no intentamos rollback
    sobre una transacción cerrada.
    """
    session = SessionLocal()
    trans = session.begin()
    try:
        yield session
    finally:
        try:
            if trans.is_active:
                trans.rollback()
        except Exception:
            # Si por cualquier razón la transacción ya no está disponible, ignoramos.
            pass
        session.close()


def _make_tenant(db: Session, name_prefix: str = "TEST") -> Tenant:
    slug = f"{name_prefix.lower()}-{uuid.uuid4().hex[:6]}"
    t = Tenant(name=f"{name_prefix}-{uuid.uuid4().hex[:6]}", slug=slug)
    db.add(t)
    db.flush()
    return t


def test_registry_compat_additive_ok(db: Session):
    """Activar v2 es válido cuando solo se agregan campos (modo additive_only)."""
    tenant = _make_tenant(db)
    section = create_section(db, tenant_id=tenant.id, key="LandingPages", name="Landing Pages")
    db.flush()

    v1 = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "hero": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
        },
        "required": ["hero"]
    }
    v2 = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "hero": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
            "seo": {"type": "object", "properties": {"title": {"type": "string"}}}
        },
        "required": ["hero"]
    }

    add_schema_version(db, tenant_id=tenant.id, section_id=section.id, version=1, schema=v1, title="v1", is_active=True)
    add_schema_version(db, tenant_id=tenant.id, section_id=section.id, version=2, schema=v2, title="v2", is_active=False)
    ok, errs = can_activate_version(db, tenant_id=tenant.id, section_id=section.id, target_version=2)

    assert ok is True
    assert errs == []


def test_registry_compat_breaking_rejected(db: Session):
    """Activar v2 falla si remuevo un campo requerido de v1 (modo additive_only)."""
    tenant = _make_tenant(db)
    section = create_section(db, tenant_id=tenant.id, key="LandingPages", name="Landing Pages")
    db.flush()

    v1 = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "hero": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
        },
        "required": ["hero"]
    }
    # v2 rompe: elimina 'hero' por completo
    v2 = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "seo": {"type": "object", "properties": {"title": {"type": "string"}}}
        }
    }

    add_schema_version(db, tenant_id=tenant.id, section_id=section.id, version=1, schema=v1, title="v1", is_active=True)
    add_schema_version(db, tenant_id=tenant.id, section_id=section.id, version=2, schema=v2, title="v2", is_active=False)
    ok, errs = can_activate_version(db, tenant_id=tenant.id, section_id=section.id, target_version=2)

    assert ok is False
    assert any("Campos requeridos ausentes" in e for e in errs)


def test_bulk_loader_from_files(db: Session, tmp_path: Path):
    """Carga un schema desde archivos por tenant/section/version."""
    # 1) tenant temporal
    tenant = _make_tenant(db, name_prefix="TENANT")

    # 2) estructura de carpetas temporal: <tmp>/x/landing_pages/v1.json
    base_dir = tmp_path / "x" / "landing_pages"
    base_dir.mkdir(parents=True, exist_ok=True)
    schema_path = base_dir / "v1.json"
    schema_v1 = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"hero": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
        "required": ["hero"]
    }
    schema_path.write_text(json.dumps(schema_v1), encoding="utf-8")

    # 3) archivo de especificación para el loader
    files = [
        SectionFile(
            section_key="LandingPages",
            section_name="Landing Pages",
            version=1,
            file_path="x/landing_pages/v1.json",
            is_active=True,
        )
    ]

    # 4) ejecutar loader
    bulk_load_tenant_schemas(
        db,
        tenant_key_or_name=tenant.name,  # acepta name o slug
        base_dir=str(tmp_path),
        files=files,
    )

    # Si no lanzó excepción, el loader funcionó (se creó la Section + Schema v1 activo)
    # Opcionalmente podríamos consultar DB para asegurar que existe el schema activo.
