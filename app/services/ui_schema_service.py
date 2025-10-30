# app/services/ui_schema_service.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.content_service import get_effective_schema
from app.services.registry_service import get_registry_for_section
from app.core.config import settings

WIDGET_TEXT_LIKE = {"string"}
WIDGET_NUMBER_LIKE = {"number", "integer"}
WIDGET_BOOL_LIKE = {"boolean"}
WIDGET_OBJECT = {"object"}
WIDGET_ARRAY = {"array"}

def _guess_widget_for_string(prop_schema: dict) -> str:
    fmt = prop_schema.get("format")
    if fmt == "uri":
        return "url"
    if fmt in ("date", "date-time"):
        return "datetime" if fmt == "date-time" else "date"
    max_len = prop_schema.get("maxLength", 0) or 0
    # textarea si no hay límite o límite alto; text si corto
    return "textarea" if (max_len == 0 or max_len > 160) else "text"

def _guess_widget(prop_schema: dict) -> str:
    t = prop_schema.get("type")
    if isinstance(t, list):
        tset = set(t)
    else:
        tset = {t} if t else set()

    if tset & WIDGET_STRING_LIKE:
        return _guess_widget_for_string(prop_schema)
    if tset & WIDGET_NUMBER_LIKE:
        return "number"
    if tset & WIDGET_BOOL_LIKE:
        return "switch"
    if tset & WIDGET_ARRAY:
        return "array"
    if tset & WIDGET_OBJECT:
        return "group"
    # Fallback
    return "text"

# alias para legibilidad (coincide con nombre de arriba)
WIDGET_STRING_LIKE = WIDGET_TEXT_LIKE

def _enum_options(prop_schema: dict) -> Optional[List[Dict[str, Any]]]:
    enum_vals = prop_schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        return [{"label": str(v), "value": v} for v in enum_vals]
    return None

def _extract_required(schema: dict) -> List[str]:
    req = schema.get("required", [])
    return [r for r in req if isinstance(r, str)]

def _ordered_fields(properties: dict, required: List[str]) -> List[str]:
    """
    Orden simple: primero campos requeridos, luego opcionales, preservando
    el orden de aparición del dict (Python 3.7+ mantiene inserción).
    """
    all_keys = list(properties.keys())
    req = [k for k in all_keys if k in required]
    opt = [k for k in all_keys if k not in required]
    return req + opt

def _build_field_ui(key: str, prop_schema: dict) -> Dict[str, Any]:
    node: Dict[str, Any] = {"key": key}

    # type y widget
    t = prop_schema.get("type")
    node["type"] = t
    node["widget"] = _guess_widget(prop_schema)

    # title/description → labels/hints
    if "title" in prop_schema:
        node["label"] = prop_schema["title"]
    if "description" in prop_schema:
        node["help"] = prop_schema["description"]

    # validation hints
    for k in ("minLength", "maxLength", "minimum", "maximum", "pattern"):
        if k in prop_schema:
            node[k] = prop_schema[k]

    # enum → select
    opts = _enum_options(prop_schema)
    if opts:
        node["widget"] = "select"
        node["options"] = opts

    # objetos/arreglos
    if prop_schema.get("type") == "object":
        props = prop_schema.get("properties", {}) or {}
        req = _extract_required(prop_schema)
        order = _ordered_fields(props, req)
        node["widget"] = "group"
        node["fields"] = [_build_field_ui(k, props[k]) for k in order]

    if prop_schema.get("type") == "array":
        items = prop_schema.get("items") or {}
        node["item"] = _build_field_ui(key=f"{key}[]", prop_schema=items)

    return node

def _overlay_registry_hints(ui_schema: Dict[str, Any], registry_section_meta: Optional[dict]) -> None:
    """
    Si el content registry provee hints (por ejemplo, mapeo de widgets por clave),
    los aplicamos como overlay no destructivo.
    Estructura esperada (flexible), ej:
    {
      "ui": {
        "fields": {
          "hero.title": {"widget": "text", "placeholder": "Título visible"},
          "hero.cta.url": {"widget": "url"}
        }
      }
    }
    """
    if not registry_section_meta:
        return
    ui_meta = (registry_section_meta or {}).get("ui") or {}
    field_overrides: dict = ui_meta.get("fields") or {}
    if not field_overrides:
        return

    # Caminamos el árbol asignando overrides por 'path' (p.ej., "hero.title")
    def apply_overrides(node: Dict[str, Any], prefix: str = "") -> None:
        key = node.get("key")
        path = f"{prefix}.{key}" if prefix and key else (key or prefix)
        if path in field_overrides:
            node.update(field_overrides[path])

        # recursión
        if node.get("widget") == "group" and isinstance(node.get("fields"), list):
            for child in node["fields"]:
                apply_overrides(child, path)
        if node.get("widget") == "array" and isinstance(node.get("item"), dict):
            apply_overrides(node["item"], f"{path}[]")

    for field in ui_schema.get("fields", []):
        apply_overrides(field, "")

def build_ui_contract(db: Session, *, tenant_id: int, section_id: int) -> Dict[str, Any]:
    """
    Devuelve un contrato UI a partir del JSON Schema activo:
    {
      "section_id": ...,
      "schema_version": ...,
      "ui_schema": { "fields": [...] },
      "hints": {
        "required": [...],
        "order": [...],
        "descriptions": {...}
      },
      "policy": {...}
    }
    """
    ss = get_effective_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        raise LookupError("No active schema found for this section.")

    schema = ss.schema or {}
    props = schema.get("properties", {}) or {}
    required = _extract_required(schema)
    order = _ordered_fields(props, required)

    # armar campos de primer nivel
    fields = [_build_field_ui(name, props[name]) for name in order]

    # descriptions
    descriptions = {k: (props[k].get("description") if hasattr(props[k], "get") else None) for k in order}

    ui_contract: Dict[str, Any] = {
        "section_id": section_id,
        "schema_version": ss.version,
        "ui_schema": {
            "fields": fields
        },
        "hints": {
            "required": required,
            "order": order,
            "descriptions": descriptions,
        },
        "policy": {
            "max_entry_data_kb": settings.MAX_ENTRY_DATA_KB,
            "idempotency_enabled": settings.IDEMPOTENCY_ENABLED,
        }
    }

    # Overlay opcional desde registry (si existe)
    reg = get_registry_for_section(db, section_id=section_id, tenant_id=tenant_id)
    _overlay_registry_hints(ui_contract["ui_schema"], reg or {})

    return ui_contract
