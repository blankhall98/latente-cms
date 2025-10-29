# app/services/registry_service.py
# Servicio: resolver registry por tenant/section, schema activo, y compat 'additive_only'
from __future__ import annotations
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.content_registry import build_registry_for_tenant, SectionMeta
from app.models.content import Section, SectionSchema

# -------- helpers DB --------
def get_section_by_id(db: Session, *, section_id: int) -> Optional[Section]:
    return db.scalar(select(Section).where(Section.id == section_id))

def get_active_schema(db: Session, *, tenant_id: int, section_id: int) -> Optional[SectionSchema]:
    return db.scalar(
        select(SectionSchema)
        .where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.is_active == True,
        )
        .limit(1)
    )

def get_schema_by_version(db: Session, *, tenant_id: int, section_id: int, version: int) -> Optional[SectionSchema]:
    return db.scalar(
        select(SectionSchema)
        .where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.version == version,
        )
        .limit(1)
    )

# -------- Registry por tenant/section --------
def get_registry_for_section(db: Session, *, section_id: int, tenant_id: int | None = None) -> SectionMeta | None:
    section = get_section_by_id(db, section_id=section_id)
    if not section:
        return None
    reg = build_registry_for_tenant(tenant_id)
    return reg.get(section.key)

# -------- Compatibilidad 'additive_only' --------
def _json_get_required(schema: dict) -> set[str]:
    req = schema.get("required", [])
    return set([r for r in req if isinstance(r, str)])

def _json_get_properties(schema: dict) -> dict:
    props = schema.get("properties", {})
    return props if isinstance(props, dict) else {}

def _field_type_descriptor(field_schema: dict) -> str:
    t = field_schema.get("type")
    if isinstance(t, list):
        return "|".join(sorted([str(x) for x in t]))
    return str(t)

def check_additive_compatibility(old_schema: dict, new_schema: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []

    old_required = _json_get_required(old_schema)
    new_required = _json_get_required(new_schema)

    old_props = _json_get_properties(old_schema)
    new_props = _json_get_properties(new_schema)

    # Requeridos viejos no pueden desaparecer del nuevo "properties"
    missing_required = [r for r in old_required if r not in new_props]
    if missing_required:
        errors.append(f"Campos requeridos ausentes en nuevo schema: {missing_required}")

    # Campos comunes no deben cambiar de tipo
    for name, old_f in old_props.items():
        if name in new_props:
            new_f = new_props[name]
            if _field_type_descriptor(old_f) != _field_type_descriptor(new_f):
                errors.append(f"Cambio de tipo en '{name}': {_field_type_descriptor(old_f)} -> {_field_type_descriptor(new_f)}")

    return (len(errors) == 0, errors)

def can_activate_version(
    db: Session,
    *,
    tenant_id: int,
    section_id: int,
    target_version: int,
) -> tuple[bool, list[str]]:
    reg = get_registry_for_section(db, section_id=section_id, tenant_id=tenant_id)
    if not reg:
        return (True, [])  # sin registry explícito, permitir

    mode = reg.get("evolution_mode", "additive_only")
    allow_breaking = reg.get("allow_breaking", False)

    current = get_active_schema(db, tenant_id=tenant_id, section_id=section_id)
    target = get_schema_by_version(db, tenant_id=tenant_id, section_id=section_id, version=target_version)

    if not target:
        return (False, [f"No existe SectionSchema version={target_version} para esta Section."])

    if not current:
        return (True, [])  # primera activación

    if mode == "additive_only" and not allow_breaking:
        ok, errs = check_additive_compatibility(current.schema, target.schema)
        return (ok, errs)

    return (True, [])
