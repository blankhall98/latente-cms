# app/services/ui_schema_service.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
import copy

from sqlalchemy.orm import Session

from app.services.content_service import get_effective_schema
from app.services.registry_service import get_registry_for_section
from app.core.settings import settings


# ----------------------------- Tipos de ayuda -----------------------------
WIDGET_TEXT_LIKE = {"string"}
WIDGET_NUMBER_LIKE = {"number", "integer"}
WIDGET_BOOL_LIKE = {"boolean"}
WIDGET_OBJECT = {"object"}
WIDGET_ARRAY = {"array"}
WIDGET_STRING_LIKE = WIDGET_TEXT_LIKE


# ----------------------------- Utilidades base -----------------------------
def _ensure_dict(d: dict, k: str) -> dict:
    if k not in d or not isinstance(d[k], dict):
        d[k] = {}
    return d[k]


def _extract_required(schema: dict) -> List[str]:
    req = schema.get("required", [])
    return [r for r in req if isinstance(r, str)]


def _normalize_order(order: Any) -> List[str]:
    """
    Acepta listas, tuplas o None. Filtra valores vacíos.
    """
    if not order:
        return []
    if isinstance(order, (list, tuple)):
        return [str(x) for x in order if x]
    return []


def _ordered_fields(properties: dict, required: List[str], ui_node: Optional[dict] = None) -> List[str]:
    """
    Ordena con prioridad:
    1) x-ui.order / ui.order / x-order (en el nodo padre)
    2) required primero
    3) resto por aparición natural
    """
    all_keys = list(properties.keys())
    # Hints de orden
    ui = ui_node or {}
    cand_orders = [
        ui.get("order"),
        ui.get("x-order"),
        ui.get("x_order"),
    ]
    # A veces llegan dentro de "ui"
    if "ui" in ui and isinstance(ui["ui"], dict):
        cand_orders.insert(0, ui["ui"].get("order"))

    explicit_order = []
    for c in cand_orders:
        o = _normalize_order(c)
        if o:
            explicit_order = o
            break

    # Aplica orden explícito, luego required no listados, luego el resto
    seen: Set[str] = set()
    out: List[str] = []

    for k in explicit_order:
        if k in properties and k not in seen:
            out.append(k)
            seen.add(k)

    for k in all_keys:
        if k in required and k not in seen:
            out.append(k); seen.add(k)

    for k in all_keys:
        if k not in seen:
            out.append(k); seen.add(k)

    return out


# ----------------------------- Heurísticas de widgets -----------------------------
def _guess_widget_for_string(prop_schema: dict) -> str:
    # Respeta x-ui.widget si ya viene
    xui = prop_schema.get("x-ui") or prop_schema.get("ui") or {}
    if isinstance(xui, dict) and "widget" in xui:
        return str(xui["widget"])

    fmt = prop_schema.get("format")
    if fmt in ("email",):
        return "email"
    if fmt in ("uri", "url"):
        return "url"
    if fmt in ("date", "date-time"):
        return "datetime" if fmt == "date-time" else "date"
    if fmt in ("color",):
        return "color"

    # Contenido media → image/video
    cmt = prop_schema.get("contentMediaType") or ""
    if isinstance(cmt, str):
        if cmt.startswith("image/"):
            return "image"
        if cmt.startswith("video/"):
            return "video"

    # Hints comunes
    name = prop_schema.get("title") or ""
    key_hint = prop_schema.get("_field_key") or ""  # lo ponemos nosotros abajo
    name_l = (name or key_hint).lower()
    if "markdown" in name_l or "md" == name_l:
        return "markdown"
    if "slug" in name_l:
        return "slug"

    max_len = prop_schema.get("maxLength", 0) or 0
    # Textareas para textos largos (o sin tope)
    return "textarea" if (max_len == 0 or max_len > 160) else "text"


def _guess_widget(prop_schema: dict) -> str:
    # Respeta x-ui.widget si existe en la raíz del prop
    xui = prop_schema.get("x-ui") or prop_schema.get("ui") or {}
    if isinstance(xui, dict) and "widget" in xui:
        return str(xui["widget"])

    # Enum → select
    if isinstance(prop_schema.get("enum"), list) and prop_schema["enum"]:
        return "select"

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


def _build_field_ui(key: str, prop_schema: dict) -> Dict[str, Any]:
    """
    Constructor del contrato UI "antiguo" (compat) — NO usado por el editor moderno,
    pero lo conservamos para endpoints existentes.
    """
    node: Dict[str, Any] = {"key": key}
    # Adjuntamos pista del nombre para heurísticas
    prop_schema = dict(prop_schema or {})
    prop_schema["_field_key"] = key

    t = prop_schema.get("type")
    node["type"] = t
    node["widget"] = _guess_widget(prop_schema)

    if "title" in prop_schema:
        node["label"] = prop_schema["title"]
    if "description" in prop_schema:
        node["help"] = prop_schema["description"]

    # Límites básicos
    for k in ("minLength", "maxLength", "minimum", "maximum", "pattern"):
        if k in prop_schema:
            node[k] = prop_schema[k]

    # Enum → select
    opts = _enum_options(prop_schema)
    if opts:
        node["widget"] = "select"
        node["options"] = opts

    # Objetos
    if prop_schema.get("type") == "object":
        props = prop_schema.get("properties", {}) or {}
        req = _extract_required(prop_schema)
        parent_ui = prop_schema.get("x-ui") or prop_schema.get("ui") or {}
        order = _ordered_fields(props, req, parent_ui)
        node["widget"] = "group"
        node["fields"] = [_build_field_ui(k, props[k]) for k in order]

    # Arrays
    if prop_schema.get("type") == "array":
        items = prop_schema.get("items") or {}
        node["item"] = _build_field_ui(key=f"{key}[]", prop_schema=items)

    return node


# ----------------------------- Overlays del registry (Contrato clásico) -----------------------------
def _overlay_registry_hints(ui_schema: Dict[str, Any], registry_section_meta: Optional[dict]) -> None:
    """
    Aplica overrides al CONTRATO UI clásico: {"ui":{"fields":{"foo.bar": { ... }}}}
    """
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


# ----------------------------- $ref / allOf resolver -----------------------------
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

        # Sustituye $ref locales
        if "$ref" in node and isinstance(node["$ref"], str):
            target = _resolve_local_ref(root, node["$ref"])
            if target is not None:
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                node.clear()
                node.update(target)
                node.update(siblings)

        # Aplana allOf triviales (solo objetos con properties/required)
        if "allOf" in node and isinstance(node["allOf"], list):
            merged: Dict[str, Any] = {}
            for part in node["allOf"]:
                part = _deref_inplace(part, root, seen)
                if isinstance(part, dict):
                    # merge superficial (lo suficiente para UI)
                    for k, v in part.items():
                        if k == "properties" and isinstance(v, dict):
                            merged.setdefault("properties", {})
                            merged["properties"].update(v)
                        elif k == "required" and isinstance(v, list):
                            merged.setdefault("required", [])
                            for r in v:
                                if r not in merged["required"]:
                                    merged["required"].append(r)
                        else:
                            merged[k] = v
            # Aplica merge sobre el nodo (conserva siblings existentes)
            siblings = {k: v for k, v in node.items() if k != "allOf"}
            node.clear()
            node.update(merged)
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


# ----------------------------- Overlays x-ui al JSON Schema (editor moderno) -----------------------------
def _normalize_field_path(path: str) -> List[str]:
    """
    Acepta variantes como:
      "sections[].data.items[].url"  |  "sections.items.data.url"  |  "data.media[].url"
    y regresa una lista de partes limpias que _apply_xui... entiende.
    """
    if not path:
        return []
    # Normaliza 'items' → '[]' cuando corresponde
    path = path.replace(".items.", "[].")
    # Evita dobles puntos
    path = path.replace("..", ".")
    parts = [p for p in path.split(".") if p]
    return parts


def _apply_xui_overlays_to_jsonschema(schema: dict, field_overrides: Dict[str, Dict[str, Any]]) -> None:
    def walk_object_props(obj_schema: dict, path_parts: List[str], payload: Dict[str, Any]) -> None:
        if not path_parts:
            xui = _ensure_dict(obj_schema, "x-ui")
            xui.update(payload or {})
            return

        head, *tail = path_parts
        props = obj_schema.get("properties") or {}

        # Array actual (cuando el tipo del padre es array y el path viene con [] al inicio)
        if head == "[]" and obj_schema.get("type") == "array":
            items_schema = _ensure_dict(obj_schema, "items")
            walk_object_props(items_schema, tail, payload)
            return

        # Campo array "foo[]" dentro de un object
        if head.endswith("[]"):
            key = head[:-2]
            prop_schema = props.get(key)
            if not isinstance(prop_schema, dict):
                return
            items = _ensure_dict(prop_schema, "items")
            walk_object_props(items, tail, payload)
            return

        # Campo normal
        prop_schema = props.get(head)
        if not isinstance(prop_schema, dict):
            return

        t = prop_schema.get("type")
        if t == "object":
            walk_object_props(prop_schema, tail, payload)
        elif t == "array":
            items = _ensure_dict(prop_schema, "items")
            walk_object_props(items, tail, payload)
        else:
            xui = _ensure_dict(prop_schema, "x-ui")
            xui.update(payload or {})

    for dotted, payload in (field_overrides or {}).items():
        if not dotted or not isinstance(payload, dict):
            continue
        parts = _normalize_field_path(dotted)
        if schema.get("type") == "object":
            walk_object_props(schema, parts, payload)


# ----------------------------- CONTRATO UI (compat, no usado por el editor) -----------------------------
def build_ui_contract(db: Session, *, tenant_id: int, section_id: int) -> Dict[str, Any]:
    ss = get_effective_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        raise LookupError("No active schema found for this section.")

    schema = ss.schema or {}
    props = schema.get("properties", {}) or {}
    required = _extract_required(schema)

    # Hints de orden a nivel raíz (x-ui en la propia sección)
    root_ui = schema.get("x-ui") or schema.get("ui") or {}
    order = _ordered_fields(props, required, root_ui)

    fields = [_build_field_ui(name, props[name]) for name in order]
    descriptions = {
        k: (props[k].get("description") if hasattr(props[k], "get") else None) for k in order
    }

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


# ----------------------------- JSON SCHEMA enriquecido (editor moderno) -----------------------------
def _post_enrich_schema_for_media(schema: dict) -> None:
    """
    Pequeño paso extra: si detectamos contentMediaType, proponemos widget image/video
    y añadimos pistas útiles (alt/preview) sin pisar lo que ya exista.
    """
    def walk(node: Any, key_name: Optional[str] = None) -> None:
        if isinstance(node, dict):
            t = node.get("type")
            if t == "string":
                cmt = node.get("contentMediaType") or ""
                if isinstance(cmt, str):
                    xui = _ensure_dict(node, "x-ui")
                    if cmt.startswith("image/") and "widget" not in xui:
                        xui["widget"] = "image"
                        xui.setdefault("preview", True)
                        if key_name and "label" not in xui:
                            xui["label"] = key_name.title()
                    elif cmt.startswith("video/") and "widget" not in xui:
                        xui["widget"] = "video"
                        xui.setdefault("preview", True)
                        if key_name and "label" not in xui:
                            xui["label"] = key_name.title()
            # Recurse
            for k, v in list(node.items()):
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)


def build_ui_jsonschema_for_active_section(db: Session, *, tenant_id: int, section_id: int) -> Dict[str, Any]:
    ss = get_effective_schema(db, tenant_id=tenant_id, section_id=section_id)
    if not ss:
        raise LookupError("No active schema found for this section.")

    schema: Dict[str, Any] = _deref_schema(ss.schema or {})

    # Overlays desde registry → x-ui (para el editor)
    reg = get_registry_for_section(db, section_id=section_id, tenant_id=tenant_id) or {}
    ui_meta = (reg.get("ui") or {})
    field_overrides: Dict[str, Dict[str, Any]] = ui_meta.get("fields") or {}
    if field_overrides:
        _apply_xui_overlays_to_jsonschema(schema, field_overrides)

    # Enriquecimiento suave para media / hints
    _post_enrich_schema_for_media(schema)

    if "$version" not in schema:
        schema["$version"] = ss.version
    return schema


# ----------------------------- Alias compatible -----------------------------
def build_ui_contract_for_active_schema(
    db: Session, *, section_id: int, tenant_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Compat con llamadas antiguas (en algunos lugares el nombre sugiere contrato UI,
    pero el editor moderno espera el JSON Schema enriquecido).
    """
    if tenant_id is None:
        raise ValueError("build_ui_contract_for_active_schema requires tenant_id for correctness.")
    return build_ui_jsonschema_for_active_section(db, tenant_id=tenant_id, section_id=section_id)


# ----------------------------- Fallback para páginas tipo objeto (ANRO) -----------------------------
_ANRO_LABELS = {
    "navbar": "Navbar",
    "hero": "Hero",
    "introduction": "Introduction",
    "intro": "Intro",
    "approach": "Approach",
    "featuredProjects": "Featured Projects",
    "ourTeam": "Our Team",
    "shapedByStory": "Shaped by Story",
    "projects": "Projects",
    "footer": "Footer",
}

_ANRO_ORDER = [
    "navbar",
    "hero",
    "introduction",
    "intro",
    "approach",
    "featuredProjects",
    "ourTeam",
    "shapedByStory",
    "projects",
    "footer",
]

_DEWA_LABELS = {
    "navbar": "Navbar",
    "hero": "Hero",
    "limitedEditionProjects": "Limited Edition Projects",
    "dewaSignatureProjects": "Dewa Signature Projects",
    "frontierProjects": "Frontier Projects",
    "arthaLegacyProjects": "Artha Legacy Projects",
    "ac2Notice": "AC2 Notice",
    "moto": "Moto",
    "whatWeDo": "What We Do",
    "dewaCapital": "Dewa Capital",
    "businessUnits": "Business Units",
    "values": "Values",
    "legacy": "Legacy",
    "dewaLegacyProjects": "Dewa Legacy Projects",
    "ourTeam": "Our Team",
    "impact": "Impact",
    "carrousel": "Carrousel",
    "footer": "Footer",
}

_DEWA_ORDER = [
    "navbar",
    "hero",
    "limitedEditionProjects",
    "dewaSignatureProjects",
    "frontierProjects",
    "arthaLegacyProjects",
    "ac2Notice",
    "moto",
    "whatWeDo",
    "dewaCapital",
    "businessUnits",
    "values",
    "legacy",
    "dewaLegacyProjects",
    "ourTeam",
    "impact",
    "carrousel",
    "footer",
]

_DEWA_DETECT_KEYS = {
    "limitedEditionProjects",
    "dewaSignatureProjects",
    "frontierProjects",
    "arthaLegacyProjects",
    "ac2Notice",
    "moto",
    "whatWeDo",
    "dewaCapital",
    "businessUnits",
    "values",
    "legacy",
    "dewaLegacyProjects",
    "impact",
    "carrousel",
}


def _schema_object_order_and_labels(schema: Dict[str, Any] | None) -> Tuple[List[str], Dict[str, str]]:
    if not isinstance(schema, dict):
        return [], {}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return [], {}

    required = _extract_required(schema)
    root_ui = schema.get("x-ui") or schema.get("ui") or {}
    order = _ordered_fields(props, required, root_ui)

    labels: Dict[str, str] = {}
    for key, node in props.items():
        if not isinstance(node, dict):
            continue
        xui = node.get("x-ui") or node.get("ui") or {}
        label = xui.get("label") if isinstance(xui, dict) else None
        if not label:
            label = node.get("title")
        if isinstance(label, str) and label.strip():
            labels[key] = label.strip()
    return order, labels


def build_sections_ui_fallback_for_object_page(
    data: Dict[str, Any],
    schema: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """
    Convierte una página estilo objeto (keys de primer nivel) al structure sections_ui
    esperado por templates/admin/page_edit.html. No modifica los datos en sí.
    """
    sections_ui: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return sections_ui

    schema_order, schema_labels = _schema_object_order_and_labels(schema)

    order = _ANRO_ORDER
    labels = dict(_ANRO_LABELS)
    ignore: set[str] = set()
    if any(k in data for k in _DEWA_DETECT_KEYS):
        order = _DEWA_ORDER
        labels = dict(_DEWA_LABELS)
        ignore.add("featuredProjects")

    if schema_order:
        order = schema_order
        labels.update(schema_labels)

    known = [k for k in order if k in data]
    extras = [
        k for k in data.keys()
        if k not in order and k not in ignore and k not in ("seo", "replace", "__draft")
    ]
    keys = known + extras

    for idx, key in enumerate(keys):
        sec = data.get(key) if key in data else {}
        label = labels.get(key, key.replace("_", " ").title())
        if isinstance(sec, dict) and "type" not in sec:
            sec = {"type": label, **sec}
        sections_ui.append({
            "index": idx,
            "label": f"{idx+1:02d} - {label}",
            "sec": sec,
            "key": key,
        })
    return sections_ui
