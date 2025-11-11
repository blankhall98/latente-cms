from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, Set

import copy
from sqlalchemy.orm import Session

from app.services.content_service import get_effective_schema
from app.services.registry_service import get_registry_for_section
from app.core.settings import settings

# --- Heurísticas de widgets (para contrato UI “antiguo”) -----------------
WIDGET_TEXT_LIKE = {"string"}
WIDGET_NUMBER_LIKE = {"number", "integer"}
WIDGET_BOOL_LIKE = {"boolean"}
WIDGET_OBJECT = {"object"}
WIDGET_ARRAY = {"array"}
WIDGET_STRING_LIKE = WIDGET_TEXT_LIKE


def _guess_widget_for_string(prop_schema: dict) -> str:
    fmt = prop_schema.get("format")
    if fmt == "uri":
        return "url"
    if fmt in ("date", "date-time"):
        return "datetime" if fmt == "date-time" else "date"
    max_len = prop_schema.get("maxLength", 0) or 0
    return "textarea" if (max_len == 0 or max_len > 160) else "text"


def _guess_widget(prop_schema: dict) -> str:
    t = prop_schema.get("type")
    tset = set(t) if isinstance(t, list) else ({t} if t else set())
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
    return "text"


def _enum_options(prop_schema: dict) -> Optional[List[Dict[str, Any]]]:
    enum_vals = prop_schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        return [{"label": str(v), "value": v} for v in enum_vals]
    return None


def _extract_required(schema: dict) -> List[str]:
    req = schema.get("required", [])
    return [r for r in req if isinstance(r, str)]


def _ordered_fields(properties: dict, required: List[str]) -> List[str]:
    all_keys = list(properties.keys())
    req = [k for k in all_keys if k in required]
    opt = [k for k in all_keys if k not in required]
    return req + opt


def _build_field_ui(key: str, prop_schema: dict) -> Dict[str, Any]:
    node: Dict[str, Any] = {"key": key}
    t = prop_schema.get("type")
    node["type"] = t
    node["widget"] = _guess_widget(prop_schema)
    if "title" in prop_schema:
        node["label"] = prop_schema["title"]
    if "description" in prop_schema:
        node["help"] = prop_schema["description"]
    for k in ("minLength", "maxLength", "minimum", "maximum", "pattern"):
        if k in prop_schema:
            node[k] = prop_schema[k]
    opts = _enum_options(prop_schema)
    if opts:
        node["widget"] = "select"
        node["options"] = opts
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


# -------------------- Overlays desde el registry (CONTRATO) --------------------
def _overlay_registry_hints(ui_schema: Dict[str, Any], registry_section_meta: Optional[dict]) -> None:
    if not registry_section_meta:
        return
    ui_meta = (registry_section_meta or {}).get("ui") or {}
    field_overrides: dict = ui_meta.get("fields") or {}
    if not field_overrides:
        return

    def apply_overrides(node: Dict[str, Any], prefix: str = "") -> None:
        key = node.get("key")
        path = f"{prefix}.{key}" if prefix and key else (key or prefix)
        if path in field_overrides:
            node.update(field_overrides[path])
        if node.get("widget") == "group" and isinstance(node.get("fields"), list):
            for child in node["fields"]:
                apply_overrides(child, path)
        if node.get("widget") == "array" and isinstance(node.get("item"), dict):
            apply_overrides(node["item"], f"{path}[]")

    for field in ui_schema.get("fields", []):
        apply_overrides(field, "")


# -------------------- Overlays x-ui dentro del JSON Schema --------------------
def _apply_xui_overlays_to_jsonschema(schema: dict, field_overrides: Dict[str, Dict[str, Any]]) -> None:
    def ensure_dict(d: dict, k: str) -> dict:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        return d[k]

    def walk_object_props(obj_schema: dict, path_parts: List[str], payload: Dict[str, Any]) -> None:
        if not path_parts:
            xui = ensure_dict(obj_schema, "x-ui")
            xui.update(payload or {})
            return
        head, *tail = path_parts
        props = obj_schema.get("properties") or {}
        if head.endswith("[]"):
            key = head[:-2]
            if obj_schema.get("type") == "array":
                items_schema = ensure_dict(obj_schema, "items")
                walk_object_props(items_schema, tail, payload)
                return
            prop_schema = props.get(key)
            if not isinstance(prop_schema, dict):
                return
            items = ensure_dict(prop_schema, "items")
            walk_object_props(items, tail, payload)
            return
        prop_schema = props.get(head)
        if not isinstance(prop_schema, dict):
            return
        t = prop_schema.get("type")
        if t == "object":
            walk_object_props(prop_schema, tail, payload)
        elif t == "array":
            items = ensure_dict(prop_schema, "items")
            walk_object_props(items, tail, payload)
        else:
            xui = ensure_dict(prop_schema, "x-ui")
            xui.update(payload or {})

    for dotted, payload in (field_overrides or {}).items():
        if not dotted or not isinstance(payload, dict):
            continue
        parts = [p for p in dotted.split(".") if p]
        if schema.get("type") == "object":
            walk_object_props(schema, parts, payload)


# -------------------- $ref resolver (local: #/$defs/...) --------------------
def _resolve_local_ref(root: dict, ref: str) -> Optional[dict]:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    node: Any = root
    for p in parts:
        if isinstance(node, dict) and p in node:
            node = node[p]
        else:
            return None
    return copy.deepcopy(node)


def _deref_inplace(node: Any, root: dict, seen: Set[int]) -> Any:
    if isinstance(node, dict):
        node_id = id(node)
        if node_id in seen:
            return node
        seen.add(node_id)

        if "$ref" in node and isinstance(node["$ref"], str):
            target = _resolve_local_ref(root, node["$ref"])
            if target is not None:
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                node.clear()
                node.update(target)
                node.update(siblings)
        for k, v in list(node.items()):
            node[k] = _deref_inplace(v, root, seen)
        return node

    if isinstance(node, list):
        for i, v in enumerate(node):
            node[i] = _deref_inplace(v, root, seen)
        return node

    return node


def _deref_schema(root_schema: dict) -> dict:
    cp = copy.deepcopy(root_schema or {})
    return _deref_inplace(cp, cp, set())


# -------------------- CONTRATO UI (opcional, no usado por el editor) --------------------
def build_ui_contract(db: Session, *, tenant_id: int, section_id: int) -> Dict[str, Any]:
    ss = get_effective_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        raise LookupError("No active schema found for this section.")
    schema = ss.schema or {}
    props = schema.get("properties", {}) or {}
    required = _extract_required(schema)
    order = _ordered_fields(props, required)
    fields = [_build_field_ui(name, props[name]) for name in order]
    descriptions = {k: (props[k].get("description") if hasattr(props[k], "get") else None) for k in order}
    ui_contract: Dict[str, Any] = {
        "section_id": section_id,
        "schema_version": ss.version,
        "ui_schema": {"fields": fields},
        "hints": {"required": required, "order": order, "descriptions": descriptions},
        "policy": {
            "max_entry_data_kb": settings.MAX_ENTRY_DATA_KB,
            "idempotency_enabled": settings.IDEMPOTENCY_ENABLED,
        },
    }
    reg = get_registry_for_section(db, section_id=section_id, tenant_id=tenant_id)
    _overlay_registry_hints(ui_contract["ui_schema"], reg or {})
    return ui_contract


# -------------------- JSON SCHEMA enriquecido con x-ui (lo que usa el editor) ---------
def build_ui_jsonschema_for_active_section(db: Session, *, tenant_id: int, section_id: int) -> Dict[str, Any]:
    ss = get_effective_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        raise LookupError("No active schema found for this section.")
    schema: Dict[str, Any] = _deref_schema(ss.schema or {})
    reg = get_registry_for_section(db, section_id=section_id, tenant_id=tenant_id) or {}
    ui_meta = (reg.get("ui") or {})
    field_overrides: Dict[str, Dict[str, Any]] = ui_meta.get("fields") or {}
    if field_overrides:
        _apply_xui_overlays_to_jsonschema(schema, field_overrides)
    if "$version" not in schema:
        schema["$version"] = ss.version
    return schema


# -------------------- Alias compatible --------------------
def build_ui_contract_for_active_schema(db: Session, *, section_id: int, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    if tenant_id is None:
        raise ValueError("build_ui_contract_for_active_schema requires tenant_id for correctness.")
    return build_ui_jsonschema_for_active_section(db, tenant_id=tenant_id, section_id=section_id)


# -------------------- Fallback for object-style pages (ANRO) --------------------
_ANRO_LABELS = {
    "navbar": "Navbar",
    "hero": "Hero",
    "intro": "Intro",
    "approach": "Approach",
    "featuredProjects": "Featured Projects",
    "footer": "Footer",
}

_ANRO_ORDER = ["navbar", "hero", "intro", "approach", "featuredProjects", "footer"]

def build_sections_ui_fallback_for_object_page(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Converts an object-style page (top-level keys) into the sections_ui structure
    expected by templates/admin/page_edit.html.
    """
    sections_ui: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return sections_ui

    known = [k for k in _ANRO_ORDER if k in data]
    extras = [k for k in data.keys() if k not in _ANRO_ORDER and k not in ("seo", "replace", "__draft")]
    keys = known + extras

    for idx, key in enumerate(keys):
        sec = data.get(key) or {}
        if isinstance(sec, dict) and "type" not in sec:
            sec = {"type": _ANRO_LABELS.get(key, key), **sec}
        label = _ANRO_LABELS.get(key, key.title())
        sections_ui.append({
            "index": idx,
            "label": f"{idx+1:02d} · {label}",
            "sec": sec,
        })
    return sections_ui
