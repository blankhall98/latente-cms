# app/web/admin/router.py
from __future__ import annotations

from typing import Any, Optional, Dict, Tuple
from datetime import date, datetime, timezone
from collections import defaultdict
import os
import re
import time
import uuid
import json

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, func, not_, or_
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import get_db
from app.models.auth import Tenant, UserTenant, UserTenantStatus, Role
from app.models.content import Section, Entry, SectionSchema
from app.models.owa_popup import OwaPopupSubmission

# Enriched JSON Schema (with x-ui) for the auto-form
from app.services.ui_schema_service import (
    build_ui_jsonschema_for_active_section,
    build_sections_ui_fallback_for_object_page,  # NEW
)
from app.services.firebase_storage import is_firebase_configured, upload_file_to_firebase

# Optional server-side schema validation toggle
ENABLE_SERVER_VALIDATION = False

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(include_in_schema=False)

# Must match auth router
SESSION_USER_KEY = "user"
SESSION_ACTIVE_TENANT_KEY = "active_tenant"


# --------------------------- Helpers ---------------------------
def _status_value(enum_cls: Any, *candidates: str) -> Any:
    for name in candidates:
        if hasattr(enum_cls, name):
            val = getattr(enum_cls, name)
            return getattr(val, "value", val)
    return candidates[-1]


def _active_status_value() -> Any:
    return _status_value(UserTenantStatus, "ACTIVE", "Active", "active")


def _require_web_user(request: Request) -> dict:
    user = (request.session or {}).get(SESSION_USER_KEY)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _get_active_tenant(request: Request) -> dict | None:
    return (request.session or {}).get(SESSION_ACTIVE_TENANT_KEY)


_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_UPLOAD_TENANT_FOLDER_MAP = {
    "dewa": "dewa-cms",
}


def _parse_upload_tenant_slugs(raw: str) -> set[str]:
    cleaned = (raw or "").strip().lower()
    if not cleaned:
        return set()
    if cleaned in ("*", "all"):
        return {"*"}
    return {s.strip().lower() for s in cleaned.split(",") if s.strip()}


def _uploads_enabled_for_tenant(active: dict | None) -> bool:
    if not is_firebase_configured():
        return False
    allowed = _parse_upload_tenant_slugs(getattr(settings, "UPLOAD_TENANT_SLUGS", ""))
    if not allowed or "*" in allowed:
        return True
    slug = ((active or {}).get("slug") or "").strip().lower()
    return slug in allowed


def _upload_context(active: dict | None) -> dict:
    return {
        "upload_enabled": _uploads_enabled_for_tenant(active),
        "upload_url": "/admin/uploads",
        "upload_max_mb": int(getattr(settings, "UPLOAD_MAX_MB", 0) or 0),
    }


def _is_owa_active(active: dict | None) -> bool:
    return ((active or {}).get("slug") or "").strip().lower() == "owa"


_AGE_BUCKETS: list[tuple[str, int | None, int | None]] = [
    ("<18", None, 17),
    ("18-24", 18, 24),
    ("25-34", 25, 34),
    ("35-44", 35, 44),
    ("45-54", 45, 54),
    ("55-64", 55, 64),
    ("65+", 65, None),
]


def _age_from_birth_date(birth_date: date) -> int:
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def _age_bucket_label(age: int) -> str:
    for label, min_age, max_age in _AGE_BUCKETS:
        if min_age is not None and age < min_age:
            continue
        if max_age is not None and age > max_age:
            continue
        return label
    return "Unknown"


def _normalize_gender_label(raw_gender: str) -> str:
    v = (raw_gender or "").strip().lower()
    if v in {"m", "male", "man", "hombre", "masculino"}:
        return "Male"
    if v in {"f", "female", "woman", "mujer", "femenino"}:
        return "Female"
    if "non" in v:
        return "Non-binary"
    if v in {"prefer not to say", "prefer_not_to_say", "prefer-not-to-say", "na", "n/a"}:
        return "Prefer not to say"
    return "Other"


def _build_owa_popup_metrics(submissions: list[OwaPopupSubmission]) -> dict[str, Any]:
    age_hist = {label: 0 for label, _, _ in _AGE_BUCKETS}
    gender_counts: dict[str, int] = defaultdict(int)
    gender_age_counts: dict[str, dict[str, int]] = defaultdict(lambda: {label: 0 for label, _, _ in _AGE_BUCKETS})
    rows: list[dict[str, Any]] = []

    for submission in submissions:
        age = _age_from_birth_date(submission.birth_date)
        age_bucket = _age_bucket_label(age)
        gender = _normalize_gender_label(submission.gender)

        age_hist[age_bucket] = age_hist.get(age_bucket, 0) + 1
        gender_counts[gender] += 1
        gender_age_counts[gender][age_bucket] = gender_age_counts[gender].get(age_bucket, 0) + 1

        rows.append(
            {
                "id": int(submission.id),
                "email": submission.email,
                "gender_raw": submission.gender,
                "gender_norm": gender,
                "birth_date": submission.birth_date.isoformat(),
                "age": age,
                "created_at": submission.created_at,
            }
        )

    return {
        "total_submissions": len(submissions),
        "age_histogram": age_hist,
        "gender_distribution": dict(sorted(gender_counts.items(), key=lambda item: item[0])),
        "gender_age_distribution": {
            gender: values for gender, values in sorted(gender_age_counts.items(), key=lambda item: item[0])
        },
        "age_buckets": [label for label, _, _ in _AGE_BUCKETS],
        "rows": rows,
    }


def _safe_segment(value: str, default: str) -> str:
    cleaned = _SEGMENT_RE.sub("-", (value or "").strip())
    cleaned = cleaned.strip("-_")
    return cleaned or default


def _upload_tenant_folder(active: dict | None, tenant_id: int) -> str:
    slug = ((active or {}).get("slug") or "").strip().lower()
    if slug in _UPLOAD_TENANT_FOLDER_MAP:
        return _UPLOAD_TENANT_FOLDER_MAP[slug]
    if slug:
        return slug
    return f"tenant-{tenant_id}"


def _set_single_project_flag(request: Request, db: Session, user: dict, projects_count: int | None = None) -> None:
    """
    Flag used by templates to hide the Projects nav when a non-superadmin has only one project.
    If projects_count is provided, avoids re-querying.
    """
    try:
        is_superadmin = bool(user.get("is_superadmin"))
    except Exception:
        is_superadmin = False
    if is_superadmin:
        request.session.pop("hide_projects_nav", None)
        return
    if projects_count is None:
        user_id = int(user["id"])
        projects_count = db.scalar(
            select(func.count(UserTenant.tenant_id))
            .where(and_(UserTenant.user_id == user_id, UserTenant.status == _active_status_value()))
        ) or 0
    request.session["hide_projects_nav"] = (projects_count == 1)


def _set_active_tenant(request: Request, tenant_id: int, tenant_slug: str, tenant_name: str) -> None:
    request.session[SESSION_ACTIVE_TENANT_KEY] = {
        "id": int(tenant_id),
        "slug": tenant_slug,
        "name": tenant_name,
    }


def _parse_int(v: Optional[str], default: int) -> int:
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _load_entry_or_404(db: Session, entry_id: int, tenant_id: int) -> tuple[Entry, Section]:
    row = db.execute(
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(and_(Entry.id == entry_id, Entry.tenant_id == tenant_id))
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Page not found in this project")
    return row  # (Entry, Section)


# --------------------------- JSON Schema helpers ---------------------------
def _get_active_schema(db: Session, section_id: int) -> Optional[SectionSchema]:
    return db.execute(
        select(SectionSchema)
        .where(and_(SectionSchema.section_id == section_id, SectionSchema.is_active == True))  # noqa: E712
        .order_by(SectionSchema.version.desc())
    ).scalars().first()


def _extract_schema_dict(ss: SectionSchema | None) -> dict:
    if not ss:
        return {}
    for attr in ("json_schema", "schema", "schema_json", "data"):
        if hasattr(ss, attr):
            val = getattr(ss, attr)
            if val is None:
                continue
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    pass
    return {}


def _deep_merge(base: Any, override: Any) -> Any:
    # dict <- dict
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = _deep_merge(base.get(k), v)
        return out

    # list <- list (override wins wholesale)
    if isinstance(base, list) and isinstance(override, list):
        return override

    # override wins only if not None
    return override if override is not None else base


def _normalize_projects_payload(payload: Any) -> dict:
    """
    Flatten __draft nesting and ensure projects is a list, not an object with a projects key.
    """
    data = payload or {}
    if not isinstance(data, dict):
        return {}
    cur = data
    # unwrap nested __draft
    while isinstance(cur, dict) and "__draft" in cur and isinstance(cur["__draft"], dict):
        cur = cur["__draft"]
    if not isinstance(cur, dict):
        return {}
    out = dict(cur)
    proj = out.get("projects")
    if isinstance(proj, dict) and "projects" in proj:
        out["projects"] = proj.get("projects") if isinstance(proj.get("projects"), list) else []
    elif proj is None:
        out["projects"] = []
    return out


def _clean_projects_list(lst: Any) -> list[dict]:
    """
    Remove null/invalid items from a projects array.
    """
    if not isinstance(lst, list):
        return []
    return [p for p in lst if isinstance(p, dict)]


def _render_projects_data(data: Any) -> dict:
    """
    If a draft exists, return draft projects; otherwise return published/root projects.
    In both cases, drop null/invalid items.
    """
    def _drop_invalid(lst):
        if not isinstance(lst, list):
            return []
        return [p for p in lst if isinstance(p, dict)]

    if not isinstance(data, dict):
        return {"projects": []}
    draft_raw = data.get("__draft") if isinstance(data.get("__draft"), dict) else None
    if draft_raw is not None:
        normalized = _normalize_projects_payload(draft_raw)
        normalized["projects"] = _drop_invalid(normalized.get("projects"))
        return normalized
    normalized = _normalize_projects_payload(data)
    normalized["projects"] = _drop_invalid(normalized.get("projects"))
    return normalized


def _render_home_data(data: Any) -> dict:
    """
    For Home page, merge draft over root so featuredProjects etc. stay visible after save.
    """
    if not isinstance(data, dict):
        return {}
    root = data if isinstance(data, dict) else {}
    draft = root.get("__draft") if isinstance(root.get("__draft"), dict) else None
    if draft:
        merged = _deep_merge(root, draft)
        merged.pop("__draft", None)
        return merged
    return root


def _render_object_page_data(data: Any) -> dict:
    """
    For object-style pages, merge draft over root so missing keys fall back to published values.
    """
    if not isinstance(data, dict):
        return {}
    draft = data.get("__draft") if isinstance(data.get("__draft"), dict) else None
    if draft:
        merged = _deep_merge(data, draft)
        merged.pop("__draft", None)
        return merged
    return data


def _resolve_local_schema_ref(root_schema: dict, ref: Any) -> dict | None:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Any = root_schema
    for part in ref[2:].split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node if isinstance(node, dict) else None


def _schema_node(schema_node: Any, root_schema: dict) -> dict:
    if not isinstance(schema_node, dict):
        return {}
    if "$ref" in schema_node:
        target = _resolve_local_schema_ref(root_schema, schema_node.get("$ref"))
        if isinstance(target, dict):
            merged = dict(target)
            for k, v in schema_node.items():
                if k != "$ref":
                    merged[k] = v
            return merged
    return schema_node


def _pick_first_string(value: Any, preferred_keys: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None

    for key in preferred_keys:
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate

    for key in ("value", "text", "title", "label", "name", "url", "href", "en", "es"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate

    for candidate in value.values():
        if isinstance(candidate, str):
            return candidate
        nested = _pick_first_string(candidate)
        if isinstance(nested, str):
            return nested
    return None


def _normalize_owa_value(
    value: Any,
    schema_node: Any,
    root_schema: dict,
    *,
    key_hint: str | None = None,
) -> Any:
    node = _schema_node(schema_node, root_schema)
    if not node:
        return value

    raw_type = node.get("type")
    if isinstance(raw_type, list):
        schema_type = next((t for t in raw_type if isinstance(t, str) and t != "null"), None)
    else:
        schema_type = raw_type if isinstance(raw_type, str) else None

    if schema_type == "string":
        raw = value
        if (
            key_hint
            and isinstance(raw, dict)
            and key_hint in raw
            and set(raw.keys()).issubset({"type", key_hint})
        ):
            raw = raw.get(key_hint)

        if isinstance(raw, str):
            return "" if raw.strip() == "[object Object]" else raw

        picked = _pick_first_string(raw, (key_hint,) if key_hint else ())
        if isinstance(picked, str):
            return "" if picked.strip() == "[object Object]" else picked

        if raw is None:
            return ""
        return str(raw)

    if schema_type == "integer":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            txt = value.strip()
            if not txt:
                return None
            try:
                return int(float(txt))
            except Exception:
                return None
        return None

    if schema_type == "number":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            txt = value.strip()
            if not txt:
                return None
            try:
                return float(txt)
            except Exception:
                return None
        return None

    if schema_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            txt = value.strip().lower()
            if txt in {"true", "1", "yes", "y", "on"}:
                return True
            if txt in {"false", "0", "no", "n", "off"}:
                return False
        return bool(value)

    if schema_type == "array":
        src = value
        if (
            key_hint
            and isinstance(src, dict)
            and key_hint in src
            and isinstance(src.get(key_hint), list)
        ):
            src = src.get(key_hint)
        if not isinstance(src, list):
            return []
        item_schema = node.get("items") or {}
        return [
            _normalize_owa_value(item, item_schema, root_schema, key_hint=None)
            for item in src
            if item is not None
        ]

    props = node.get("properties") if isinstance(node.get("properties"), dict) else None
    if schema_type == "object" or isinstance(props, dict):
        props = props or {}
        src = value
        if (
            key_hint
            and isinstance(src, dict)
            and key_hint in src
            and set(src.keys()).issubset({"type", key_hint})
        ):
            src = src.get(key_hint)
        if not isinstance(src, dict):
            src = {}

        out: dict[str, Any] = {}
        for prop_key, prop_schema in props.items():
            if prop_key in src:
                out[prop_key] = _normalize_owa_value(
                    src.get(prop_key), prop_schema, root_schema, key_hint=prop_key
                )

        # Keep required const/enum type markers only when schema actually defines them.
        if "type" in props and "type" not in out:
            type_node = _schema_node(props.get("type"), root_schema)
            if "const" in type_node:
                out["type"] = type_node.get("const")
            elif isinstance(type_node.get("enum"), list) and len(type_node["enum"]) == 1:
                out["type"] = type_node["enum"][0]

        return out

    return value


def _normalize_owa_payload(payload: Any, json_schema: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    if not isinstance(json_schema, dict):
        return dict(payload)

    normalized = _normalize_owa_value(payload, json_schema, json_schema, key_hint=None)
    out = normalized if isinstance(normalized, dict) else {}

    # Preserve editor meta keys that may live outside strict section schema.
    for k in ("seo", "replace"):
        if k in payload and k not in out:
            out[k] = payload[k]
    return out


def _is_effectively_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (int, float, bool)):
        return False
    if isinstance(value, list):
        return all(_is_effectively_empty(v) for v in value)
    if isinstance(value, dict):
        if not value:
            return True
        return all(_is_effectively_empty(v) for v in value.values())
    return False


def _is_blank_project(obj: Any) -> bool:
    if obj is None:
        return True
    if not isinstance(obj, dict):
        return False
    if not obj:
        return True
    for k, v in obj.items():
        if k == "category":
            continue
        if not _is_effectively_empty(v):
            return False
    return True


def _sanitize_dewa_projects_payload(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    keys = {
        "limitedEditionProjects",
        "dewaSignatureProjects",
        "frontierProjects",
        "arthaLegacyProjects",
    }
    for key in keys:
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        lst = block.get("projectsList")
        if not isinstance(lst, list):
            continue
        cleaned = []
        for item in lst:
            if item is None:
                continue
            if isinstance(item, dict) and _is_blank_project(item):
                continue
            cleaned.append(item)
        block["projectsList"] = cleaned
    return data


def _normalize_privacy_payload(payload: Any, existing: dict | None = None) -> dict:
    """
    Ensure privacy policy payload is a simple object with a string body and optional seo/replace.
    Avoid wiping content when an empty/invalid payload arrives.
    """
    base = existing or {}
    if not isinstance(base, dict):
        base = {}
    out: Dict[str, Any] = {}

    if isinstance(payload, dict):
        body_val = payload.get("body", payload.get("content"))
        out["body"] = "" if body_val is None else str(body_val)
        if "seo" in payload and isinstance(payload.get("seo"), dict):
            out["seo"] = payload["seo"]
        elif isinstance(base.get("seo"), dict):
            out["seo"] = base["seo"]
        out["replace"] = bool(payload.get("replace", base.get("replace", False)))
    else:
        out["body"] = str(payload) if payload is not None else ""
        if isinstance(base.get("seo"), dict):
            out["seo"] = base["seo"]
        out["replace"] = bool(base.get("replace", False))

    # Fallback to existing body if incoming is empty and existing had content
    if (out.get("body", "") == "") and isinstance(base.get("body"), str) and base.get("body"):
        out["body"] = base["body"]

    return out


def _defaults_from_schema(schema: dict) -> Any:
    if not isinstance(schema, dict):
        return None

    if "default" in schema:
        return schema["default"]

    t = schema.get("type")

    for union_key in ("oneOf", "anyOf", "allOf"):
        if union_key in schema and isinstance(schema[union_key], list) and schema[union_key]:
            return _defaults_from_schema(schema[union_key][0])

    if t == "object":
        props = schema.get("properties", {}) or {}
        out: Dict[str, Any] = {}
        for k, sub in props.items():
            out[k] = _defaults_from_schema(sub)
        return out

    if t == "array":
        if "default" in schema:
            return schema["default"]
        return []

    if t == "string":
        return ""
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False

    if "properties" in schema:
        return _defaults_from_schema({"type": "object", "properties": schema["properties"]})
    if "items" in schema:
        return _defaults_from_schema({"type": "array", **({"default": []} if "default" not in schema else {})})

    return None


def _build_form_model_from_active_schema(json_schema: dict, entry_data: dict) -> Tuple[dict, int]:
    defaults = _defaults_from_schema(json_schema) or {}
    merged = _deep_merge(defaults, entry_data or {})
    schema_version = json_schema.get("$version") or json_schema.get("version") or 1
    return merged, int(schema_version)


def _validate_against_schema(json_schema: dict, data_obj: dict) -> list[str]:
    if not ENABLE_SERVER_VALIDATION:
        return []
    # Hook: integrate Draft 2020-12 if you want strict server-side validation
    return []


# --------------------------- Dashboard ---------------------------
@router.get("/admin")
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        auth = _require_web_user(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    user_id = int(auth["id"])
    is_superadmin = bool(auth.get("is_superadmin"))
    active = _get_active_tenant(request)

    if not active:
        kpis = [
            {"label": "Pages", "value": "0", "suffix": "published"},
            {"label": "Sections", "value": "0", "suffix": "in project"},
            {"label": "Projects", "value": "0", "suffix": "available"},
        ]
        return templates.TemplateResponse(
            "admin/dashboard.html",
            {
                "request": request,
                "user": {"email": auth.get("email")},
                "kpis": kpis,
                "recent_entries": [],
                "quick_links": [
                    {"href": "/admin/projects", "title": "Browse Projects", "sub": "Switch or set your active project"},
                ],
                "current_tenant": {"name": "-", "slug": None, "id": None},
            },
        )

    tenant_id = int(active["id"])

    if is_superadmin:
        projects_count = db.scalar(select(func.count(Tenant.id)))
    else:
        projects_count = db.scalar(
            select(func.count(UserTenant.tenant_id))
            .where(and_(UserTenant.user_id == user_id, UserTenant.status == _active_status_value()))
        ) or 0

    _set_single_project_flag(request, db, auth, projects_count)

    sections_count = db.scalar(
        select(func.count(Section.id)).where(Section.tenant_id == tenant_id)
    ) or 0

    PUBLISHED = "published"
    if _is_owa_active(active):
        pages_published = db.scalar(
            select(func.count(Entry.id))
            .join(Section, Section.id == Entry.section_id)
            .where(
                and_(
                    Entry.tenant_id == tenant_id,
                    Entry.status == PUBLISHED,
                    not_(and_(Section.key == "landing_pages", Entry.slug == "home")),
                )
            )
        ) or 0
    else:
        pages_published = db.scalar(
            select(func.count(Entry.id)).where(
                and_(Entry.tenant_id == tenant_id, Entry.status == PUBLISHED)
            )
        ) or 0

    kpis = [
        {"label": "Pages", "value": str(pages_published), "suffix": "published"},
        {"label": "Sections", "value": str(sections_count), "suffix": "in project"},
        {"label": "Projects", "value": str(projects_count), "suffix": "available"},
    ]

    try:
        order_cols = [Entry.updated_at.desc().nullslast(), Entry.id.desc()]
    except Exception:
        order_cols = [Entry.id.desc()]

    recent_query = (
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(Entry.tenant_id == tenant_id)
    )
    if _is_owa_active(active):
        recent_query = recent_query.where(not_(and_(Section.key == "landing_pages", Entry.slug == "home")))
    rows = db.execute(recent_query.order_by(*order_cols).limit(5)).all()

    recent_entries = []
    for e, s in rows:
        status_text = getattr(e.status, "value", e.status)
        section_key = getattr(s, "key", getattr(s, "name", "Section"))
        tenant_slug = active.get("slug", "")
        title = (e.data or {}).get("title") or e.slug or f"Page {getattr(e, 'id', '')}"
        recent_entries.append({
            "title": title,
            "sub": f"{section_key} / {tenant_slug} - {status_text}",
            "id": int(getattr(e, "id", 0)) if getattr(e, "id", None) else None,
        })

    quick_links = [
        {"href": "/admin/projects", "title": "Browse Projects", "sub": "Switch between your projects"},
        {"href": "/admin/pages", "title": "All Pages", "sub": "View and edit pages"},
    ]
    if (not is_superadmin) and projects_count == 1:
        quick_links = [
            {"href": "/admin/pages", "title": "All Pages", "sub": "View and edit pages"},
        ]

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": {"email": auth.get("email")},
            "kpis": kpis,
            "recent_entries": recent_entries,
            "quick_links": quick_links,
            "current_tenant": active,
        },
    )


# --------------------------- Projects ---------------------------
@router.get("/admin/projects")
def projects_list(request: Request, db: Session = Depends(get_db)):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active_val = _active_status_value()

    if is_superadmin:
        rows = db.execute(select(Tenant).order_by(Tenant.name.asc())).all()
        items = [{
            "id": t.id,
            "name": t.name,
            "slug": t.slug,
            "role": "superadmin",
            "role_label": "Superadmin",
            "status": "active",
        } for (t,) in rows]
    else:
        q = (
            select(Tenant, UserTenant, Role)
            .join(UserTenant, UserTenant.tenant_id == Tenant.id)
            .join(Role, Role.id == UserTenant.role_id)
            .where(
                and_(
                    UserTenant.user_id == int(user["id"]),
                    UserTenant.status == active_val,
                )
            )
            .order_by(Tenant.name.asc())
        )
        rows = db.execute(q).all()
        items = []
        for t, ut, r in rows:
            items.append({
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "role": r.key,
                "role_label": getattr(r, "label", r.key).title() if getattr(r, "label", None) else r.key,
                "status": getattr(ut.status, "value", ut.status),
            })

    _set_single_project_flag(request, db, user, len(items))

    if (not is_superadmin) and len(items) == 1:
        only = items[0]
        _set_active_tenant(request, only["id"], only["slug"], only["name"])
        return RedirectResponse(url="/admin/pages", status_code=302)

    current = _get_active_tenant(request)
    return templates.TemplateResponse(
        "admin/projects.html",
        {
            "request": request,
            "user": user,
            "projects": items,
            "active_tenant": current,
        },
    )


@router.post("/admin/projects/{tenant_id}/set-active")
def set_active_project(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active_val = _active_status_value()

    if is_superadmin:
        t = db.get(Tenant, tenant_id)
        if not t:
            raise HTTPException(status_code=404, detail="Project not found.")
        _set_active_tenant(request, t.id, t.slug, t.name)
        return RedirectResponse(url="/admin", status_code=303)

    tu = db.execute(
        select(Tenant, UserTenant)
        .where(
            and_(
                Tenant.id == tenant_id,
                UserTenant.tenant_id == Tenant.id,
                UserTenant.user_id == int(user["id"]),
                UserTenant.status == active_val,
            )
        )
    ).first()

    if not tu:
        raise HTTPException(status_code=403, detail="You don't have access to this project.")

    tenant, _ = tu
    _set_active_tenant(request, tenant.id, tenant.slug, tenant.name)
    return RedirectResponse(url="/admin", status_code=303)


# --------------------------- Pages list ---------------------------
@router.get("/admin/pages")
def pages_list(
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: Optional[int] = Query(default=None),
):
    user = (request.session or {}).get(SESSION_USER_KEY)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    active = _get_active_tenant(request)
    tid = int(tenant_id or (active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    q: Optional[str] = request.query_params.get("q")
    status_param: Optional[str] = request.query_params.get("status")
    section_id_param: Optional[str] = request.query_params.get("section")
    page = _parse_int(request.query_params.get("page"), 1)
    per_page = _parse_int(request.query_params.get("per_page"), 10)
    page = max(page, 1)
    per_page = max(min(per_page, 50), 5)
    offset = (page - 1) * per_page

    base = (
        select(Entry, Section)
        .join(Section, Section.id == Entry.section_id)
        .where(Entry.tenant_id == tid)
    )
    if _is_owa_active(active):
        base = base.where(not_(and_(Section.key == "landing_pages", Entry.slug == "home")))

    VALID_STATUS = {"published", "draft", "archived"}
    if status_param:
        s = status_param.strip().lower()
        if s in VALID_STATUS:
            base = base.where(Entry.status == s)

    if section_id_param:
        try:
            sid = int(section_id_param)
            base = base.where(Entry.section_id == sid)
        except Exception:
            pass

    if q:
        ilike_term = f"%{q.strip()}%"
        try:
            base = base.where(
                or_(
                    Entry.slug.ilike(ilike_term),
                    Entry.data["title"].astext.ilike(ilike_term),
                )
            )
        except Exception:
            base = base.where(Entry.slug.ilike(ilike_term))

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    try:
        order_cols = [Entry.updated_at.desc().nullslast(), Entry.id.desc()]
    except Exception:
        order_cols = [Entry.id.desc()]
    rows = db.execute(base.order_by(*order_cols).limit(per_page).offset(offset)).all()

    sects_query = select(Section.id, Section.name).where(Section.tenant_id == tid)
    if _is_owa_active(active):
        sects_query = sects_query.where(Section.key != "landing_pages")
    sects = db.execute(sects_query.order_by(Section.name.asc())).all()

    items = []
    for e, s in rows:
        title = (e.data or {}).get("title") or e.slug
        items.append({
            "id": e.id,
            "title": title,
            "slug": e.slug,
            "section_name": s.name,
            "status": e.status,
            "updated_at": e.updated_at,
        })

    next_page = page + 1 if (offset + len(items)) < total else None
    prev_page = page - 1 if page > 1 else None

    return templates.TemplateResponse(
        "admin/pages.html",
        {
            "request": request,
            "user": user,
            "active_tenant": active,
            "items": items,
            "sections": [{"id": sid, "name": sname} for sid, sname in sects],
            "filters": {
                "q": q or "",
                "status": (status_param or "").lower(),
                "section": section_id_param or "",
            },
            "page": page,
            "per_page": per_page,
            "next_page": next_page,
            "prev_page": prev_page,
        },
    )


# --------------------------- Page detail (read-only shell) ---------------------------
@router.get("/admin/pages/{entry_id}")
def page_detail(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: Optional[int] = Query(default=None),
    section_tab: Optional[str] = Query(default=None),
):
    user = _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int(tenant_id or (active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    data = entry.data or {}
    keys = list(data.keys())

    preferred_first = ["hero", "header", "intro", "title", "content", "body"]

    def _priority(k: str) -> tuple[int, str]:
        return (preferred_first.index(k) if k in preferred_first else 999, k)

    keys_sorted = sorted(keys, key=_priority)
    current_tab = section_tab or (keys_sorted[0] if keys_sorted else "content")

    sections_nav = [{"key": k, "label": k.replace("_", " ").title()} for k in keys_sorted]
    current_payload = data.get(current_tab, data if current_tab == "content" else "")

    ss_active = _get_active_schema(db, section.id)

    return templates.TemplateResponse(
        "admin/page_detail.html",
        {
            "request": request,
            "user": user,
            "active_tenant": active,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (data.get("title") or data.get("name") or entry.slug),
                "status": entry.status,
                "section_name": section.name,
                "updated_at": entry.updated_at,
                "schema_version": (ss_active.version if ss_active else entry.schema_version),
            },
            "sections_nav": sections_nav,
            "current_tab": current_tab,
            "current_payload": current_payload,
        },
    )


# --------------------------- Page Editor (Active Schema-driven) ---------------------------
@router.get("/admin/pages/{entry_id}/edit")
def page_edit_get(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    if section.key == "pop_up":
        submissions = db.scalars(
            select(OwaPopupSubmission)
            .where(OwaPopupSubmission.tenant_id == tid)
            .order_by(OwaPopupSubmission.created_at.desc())
        ).all()
        metrics = _build_owa_popup_metrics(submissions)
        ss = _get_active_schema(db, section.id)

        return templates.TemplateResponse(
            "admin/owa_popup.html",
            {
                "request": request,
                "user": {"id": int(user["id"]), "email": user.get("email")},
                "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
                "is_superadmin": is_superadmin,
                "page": {
                    "id": entry.id,
                    "slug": entry.slug,
                    "title": "OWA Pop-Up Submissions",
                    "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "schema_version": (ss.version if ss else entry.schema_version),
                    "section_id": int(section.id),
                },
                "popup_endpoint": f"{settings.API_V1_STR}/owa/popup-submissions",
                "popup_metrics": metrics,
            },
        )

    # If page is published and has __draft, edit the draft (except projects)
    base_data = entry.data or {}
    is_published = (getattr(entry, "status", "draft") == "published")
    if section.key == "projects":
        working_data = _render_projects_data(base_data)
    elif section.key == "privacy_policy":
        # Prefer draft if exists, but always normalize to a simple object
        working_candidate = base_data.get("__draft") if (is_published and isinstance(base_data.get("__draft"), dict)) else base_data
        working_data = _normalize_privacy_payload(working_candidate, base_data if isinstance(base_data, dict) else {})
    elif section.key == "home":
        working_data = _render_home_data(base_data)
    else:
        working_data = _render_object_page_data(base_data) if is_published else base_data

    # UI JSON Schema (enriched) for auto-form
    try:
        schema_ui_dict = build_ui_jsonschema_for_active_section(db, tenant_id=tid, section_id=section.id)
        schema_ui_json = json.dumps(schema_ui_dict, ensure_ascii=False)
        ss_version = schema_ui_dict.get("$version") or schema_ui_dict.get("version")
    except Exception:
        schema_ui_json = ""
        ss_version = None

    # Initial model (defaults merged with current data)
    ss = _get_active_schema(db, section.id)
    json_schema = _extract_schema_dict(ss)
    if _is_owa_active(active) and getattr(section, "key", "") != "landing_pages":
        working_data = _normalize_owa_payload(working_data or {}, json_schema)
    form_model, _ = _build_form_model_from_active_schema(json_schema, working_data or {})

    replace_val = bool((form_model.get("replace") or False))
    seo = form_model.get("seo") or {}
    seo_title = seo.get("title") or ""
    seo_desc = seo.get("description") or ""

    raw_sections = form_model.get("sections") or []
    sections_ui = []
    if section.key == "privacy_policy":
        # Single body field; force a simple panel keyed to privacy_policy with body inside
        sections_ui = [{
            "index": 0,
            "label": "01 - Privacy Policy",
            "sec": {"body": form_model.get("body", "")},
            "key": "privacy_policy",
        }]
    elif section.key == "projects":
        sections_ui = [{
            "index": 0,
            "label": "01 - Projects",
            "sec": {"projects": form_model.get("projects", [])},
            "key": "projects",
        }]
    elif isinstance(raw_sections, list) and raw_sections:
        for i, sec in enumerate(raw_sections):
            t = (sec or {}).get("type") or "Block"
            heading = (sec or {}).get("heading") or ""
            label = f"{i+1:02d} - {t}" + (f" | {heading}" if heading else "")
            sections_ui.append({"index": i, "label": label, "sec": sec, "key": (sec or {}).get("type") })
    else:
        # Fallback for object-style pages (ANRO)
        sections_ui = build_sections_ui_fallback_for_object_page(form_model)

    if getattr(section, "key", "") == "home":
        entry_json_for_client = _render_home_data(entry.data)
    elif getattr(section, "key", "") == "projects":
        entry_json_for_client = _render_projects_data(entry.data)
    else:
        entry_json_for_client = working_data if working_data is not None else (entry.data or {})

    return templates.TemplateResponse(
        "admin/page_edit.html",
        {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (form_model.get("title") or form_model.get("name") or entry.slug),
                "status": entry.status,
                "section_name": section.name,
                "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                "schema_version": (ss.version if ss else entry.schema_version),
                "section_id": int(section.id),
            },
            "replace_val": replace_val,
            "seo_title": seo_title,
            "seo_desc": seo_desc,
            "sections_ui": sections_ui,
            "schema_ui_json": schema_ui_json,  # serialized
            "error": None,
            "ok_message": None,
            "__entry_data_json": json.dumps(entry_json_for_client or {}, ensure_ascii=False),
            **_upload_context(active),
        },
    )


@router.post("/admin/pages/{entry_id}/edit")
def page_edit_post(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    content_json: str = Form(...),
):
    user = _require_web_user(request)
    is_superadmin = bool(user.get("is_superadmin"))
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)

    if section.key == "pop_up":
        return RedirectResponse(url=f"/admin/pages/{entry_id}/edit", status_code=302)

    # UI JSON Schema (also for POST)
    try:
        schema_ui_dict = build_ui_jsonschema_for_active_section(db, tenant_id=tid, section_id=section.id)
        schema_ui_json = json.dumps(schema_ui_dict, ensure_ascii=False)
        ui_version = schema_ui_dict.get("$version") or schema_ui_dict.get("version")
    except Exception:
        schema_ui_json = ""
        ui_version = None

    ss = _get_active_schema(db, section.id)
    json_schema = _extract_schema_dict(ss)
    active_version = (ui_version if ui_version is not None else (ss.version if ss else entry.schema_version))

    def _schema_has_property(schema: dict, key: str) -> bool:
        if not isinstance(schema, dict):
            return False
        props = schema.get("properties")
        return isinstance(props, dict) and key in props

    # --- Safe parse
    try:
        parsed = json.loads(content_json)
        if not isinstance(parsed, dict):
            raise ValueError("Submitted payload must be a JSON object.")
    except Exception as e:
        data = entry.data or {}
        sections = data.get("sections") or []
        sections_ui = []
        if sections:
            sections_ui = [{
                "index": i,
                "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
                "sec": (blk or {}),
            } for i, blk in enumerate(sections)]
        else:
            sections_ui = build_sections_ui_fallback_for_object_page(data)

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (data.get("title") or data.get("name") or entry.slug),
                "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": content_json,
                "replace_val": bool(data.get("replace", False)),
                "seo_title": (data.get("seo") or {}).get("title", ""),
                "seo_desc": (data.get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
                "schema_ui_json": schema_ui_json,
                "error": f"Invalid JSON: {e}",
                "ok_message": None,
                **_upload_context(active),
            },
            status_code=400,
        )

    # --- Minimal validation (pluggable)
    if _is_owa_active(active) and getattr(section, "key", "") != "landing_pages":
        parsed = _normalize_owa_payload(parsed, json_schema)

    errors = _validate_against_schema(json_schema, parsed)
    if errors:
        sections = parsed.get("sections") or []
        sections_ui = []
        if sections:
            sections_ui = [{
                "index": i,
                "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
                "sec": (blk or {}),
            } for i, blk in enumerate(sections)]
        else:
            sections_ui = build_sections_ui_fallback_for_object_page(parsed)

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (parsed.get("title") or (entry.data or {}).get("title") or entry.slug),
                "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": json.dumps(parsed, ensure_ascii=False, indent=2),
                "replace_val": bool(parsed.get("replace", False)),
                "seo_title": (parsed.get("seo") or {}).get("title", ""),
                "seo_desc": (parsed.get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
                "schema_ui_json": schema_ui_json,
                "error": "Schema validation failed: " + "; ".join(errors[:5]),
                "ok_message": None,
                **_upload_context(active),
            },
            status_code=422,
        )

    # ----------------------------- Anti-wipe rules -----------------------------
    base_data = entry.data or {}
    payload = parsed or {}
    is_published_now = (getattr(entry, "status", "draft") == "published")

    # Special handling: projects section
    if getattr(section, "key", "") == "projects":
        base_projects_data = _normalize_projects_payload(entry.data or {})
        incoming_projects = _normalize_projects_payload(payload)
        # If projects key is missing, fall back to existing; but if it is present (even empty), respect it.
        if "projects" not in incoming_projects:
            incoming_projects["projects"] = []
        elif incoming_projects.get("projects") is None:
            incoming_projects["projects"] = []
        incoming_projects["projects"] = _clean_projects_list(incoming_projects.get("projects"))
        if "seo" not in incoming_projects and base_projects_data.get("seo"):
            incoming_projects["seo"] = base_projects_data["seo"]
        if "replace" not in incoming_projects:
            incoming_projects["replace"] = False

        now = datetime.now(timezone.utc)
        if is_published_now:
            base_clean = dict(entry.data) if isinstance(entry.data, dict) else {}
            base_clean.pop("__draft", None)
            base_clean["__draft"] = incoming_projects
            entry.data = base_clean
        else:
            entry.data = incoming_projects
        entry.schema_version = active_version
        entry.updated_at = now
        db.add(entry)
        db.commit()
        db.refresh(entry)

        working_after = _render_projects_data(entry.data)
        sections_ui = [{
            "index": 0,
            "label": "01 - Projects",
            "sec": {"projects": (working_after or {}).get("projects", [])},
            "key": "projects",
        }]
        entry_json_for_client = _render_projects_data(entry.data)
        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
                "request": request,
                "user": {"id": int(user["id"]), "email": user.get("email")},
                "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
                "is_superadmin": is_superadmin,
                "page": {
                    "id": entry.id,
                    "slug": entry.slug,
                    "title": (working_after or {}).get("title") or entry.slug,
                    "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": json.dumps(working_after or {}, ensure_ascii=False, indent=2),
                "replace_val": bool((working_after or {}).get("replace", False)),
                "seo_title": ((working_after or {}).get("seo") or {}).get("title", ""),
                "seo_desc": ((working_after or {}).get("seo") or {}).get("description", ""),
                "sections_ui": sections_ui,
        "schema_ui_json": schema_ui_json,
        "error": None,
        "ok_message": "Changes saved.",
        "__entry_data_json": json.dumps(
            (_render_home_data(entry.data) if getattr(section, "key", "") == "home" else entry_json_for_client) or {},
            ensure_ascii=False
        ),
        **_upload_context(active),
    },
)

    incoming_has_sections_key = "sections" in payload
    incoming_sections = payload.get("sections", None)
    replace_flag = bool(payload.get("replace", False))
    if incoming_has_sections_key and isinstance(incoming_sections, list) and len(incoming_sections) == 0 and not replace_flag:
        # Build UI list from either sections[] or object-style
        sections_ui = [{
            "index": i,
            "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
            "sec": (blk or {}),
        } for i, blk in enumerate(base_data.get("sections") or [])] or build_sections_ui_fallback_for_object_page(base_data)

        return templates.TemplateResponse(
            "admin/page_edit.html",
            {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (base_data.get("title") or entry.slug),
                "status": entry.status,
                    "section_name": section.name,
                    "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                    "section_id": int(section.id),
                    "schema_version": active_version,
                },
                "initial_json": json.dumps(payload, ensure_ascii=False, indent=2),
                "replace_val": replace_flag,
                "seo_title": (payload.get("seo") or {}).get("title", (base_data.get("seo") or {}).get("title", "")),
                "seo_desc": (payload.get("seo") or {}).get("description", (base_data.get("seo") or {}).get("description", "")),
                "sections_ui": sections_ui,
                "schema_ui_json": schema_ui_json,
                "error": "Cannot clear sections without replace=true.",
                "ok_message": None,
                **_upload_context(active),
            },
            status_code=400,
        )

    # Non-destructive merge (draft-aware)
    is_published_now = (getattr(entry, "status", "draft") == "published")
    home_supports_featured = _schema_has_property(json_schema, "featuredProjects")

    def _unwrap_draft(d: Any) -> Any:
        cur = d
        while isinstance(cur, dict) and "__draft" in cur:
            nxt = cur.get("__draft")
            if not isinstance(nxt, dict):
                break
            cur = nxt
        return cur

    working_base = _unwrap_draft(base_data.get("__draft")) if (is_published_now and isinstance(base_data.get("__draft"), dict)) else _unwrap_draft(base_data)
    if getattr(section, "key", "") == "home":
        # Home: use merged view (draft over root) so featuredProjects don't vanish
        working_base = _render_home_data(base_data)
    if isinstance(working_base, dict) and "__draft" in working_base:
        working_base = {k: v for k, v in working_base.items() if k != "__draft"}
    if getattr(section, "key", "") == "home" and home_supports_featured:
        # Home: carry featuredProjects exactly as submitted; if missing, keep existing
        merged = dict(working_base) if isinstance(working_base, dict) else {}
        existing_fp = merged.get("featuredProjects") if isinstance(merged, dict) else []
        incoming_fp_present = isinstance(payload, dict) and "featuredProjects" in payload
        if incoming_fp_present:
            merged["featuredProjects"] = payload.get("featuredProjects") if isinstance(payload.get("featuredProjects"), list) else []
        else:
            merged["featuredProjects"] = existing_fp if isinstance(existing_fp, list) else []
        if isinstance(payload, dict):
            for k, v in payload.items():
                if k == "featuredProjects":
                    continue
                merged[k] = _deep_merge(merged.get(k), v)
    else:
        merged = _deep_merge(working_base, payload)
        if getattr(section, "key", "") == "home" and isinstance(merged, dict):
            merged.pop("featuredProjects", None)
    if not incoming_has_sections_key and "sections" in working_base:
        merged["sections"] = working_base["sections"]
    if isinstance(merged, dict) and "__draft" in merged:
        merged.pop("__draft", None)

    # Clean project lists (DEWA keys only) to avoid null/blank reappearing items
    merged = _sanitize_dewa_projects_payload(merged)

    # Persist (draft vs root)
    is_published_now = (getattr(entry, "status", "draft") == "published")
    if getattr(section, "key", "") == "projects":
        # Projects: if published, stash into __draft so delivery stays stable until publish
        if is_published_now:
            base_clean = dict(base_data) if isinstance(base_data, dict) else {}
            base_clean.pop("__draft", None)
            base_clean["__draft"] = merged
            entry.data = base_clean
        else:
            entry.data = merged
    elif getattr(section, "key", "") == "privacy_policy":
        # Privacy Policy: accept both {body:...} and {privacy_policy:{body:...}}
        incoming = payload
        if isinstance(payload, dict) and "privacy_policy" in payload:
            pp = payload.get("privacy_policy")
            if isinstance(pp, dict):
                incoming = {**payload, **pp}
            elif isinstance(pp, str):
                incoming = {**payload, "body": pp}
        merged = _normalize_privacy_payload(incoming, base_data if isinstance(base_data, dict) else {})
        if is_published_now:
            # If already published, stash edits in __draft so delivery stays unchanged until publish
            base_clean = dict(base_data) if isinstance(base_data, dict) else {}
            base_clean.pop("__draft", None)
            base_clean["__draft"] = merged
            entry.data = base_clean
        else:
            entry.data = merged
    elif is_published_now:
        base_clean = dict(base_data)
        base_clean.pop("__draft", None)
        base_clean["__draft"] = merged
        entry.data = base_clean
    else:
        entry.data = merged

    entry.schema_version = active_version
    entry.updated_at = datetime.now(timezone.utc)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # Rebuild UI bits after save (based on current working data)
    current_base = entry.data or {}
    if is_published_now and isinstance(current_base, dict) and isinstance(current_base.get("__draft"), dict):
        working_after = _render_object_page_data(current_base)
    else:
        working_after = current_base

    if getattr(section, "key", "") == "privacy_policy":
        sections_ui = [{
            "index": 0,
            "label": "01 - Privacy Policy",
            "sec": {"body": working_after.get("body", "")},
            "key": "privacy_policy",
        }]
    elif getattr(section, "key", "") == "home":
        wa = _render_home_data(entry.data)
        working_after = wa  # ensure initial_json and SEO values reflect merged view
        sections_ui = build_sections_ui_fallback_for_object_page(wa)
    elif getattr(section, "key", "") == "projects":
        sections_ui = [{
            "index": 0,
            "label": "01 - Projects",
            "sec": {"projects": (working_after or {}).get("projects", [])},
            "key": "projects",
        }]
    else:
        sections = (working_after.get("sections") or [])
        sections_ui = [{
            "index": i,
            "label": f"{i+1:02d} - {(blk or {}).get('type','Section')}" + (f" | { (blk or {}).get('heading','') }" if (blk or {}).get('heading') else ""),
            "sec": (blk or {}),
        } for i, blk in enumerate(sections)] or build_sections_ui_fallback_for_object_page(working_after)

    if getattr(section, "key", "") == "home":
        entry_json_for_client = _render_home_data(entry.data)
    elif getattr(section, "key", "") == "projects":
        entry_json_for_client = _render_projects_data(entry.data)
    else:
        entry_json_for_client = working_after if working_after is not None else (entry.data or {})

    return templates.TemplateResponse(
        "admin/page_edit.html",
        {
            "request": request,
            "user": {"id": int(user["id"]), "email": user.get("email")},
            "active_tenant": {"id": int(active["id"]), "slug": active["slug"], "name": active["name"]},
            "is_superadmin": is_superadmin,
            "page": {
                "id": entry.id,
                "slug": entry.slug,
                "title": (working_after or {}).get("title") or entry.slug,
                "status": entry.status,
                "section_name": section.name,
                "section_key": getattr(section, "key", getattr(section, "name", "Section")),
                "section_id": int(section.id),
                "schema_version": active_version,
            },
            "initial_json": json.dumps(working_after or {}, ensure_ascii=False, indent=2),
            "replace_val": bool((working_after or {}).get("replace", False)),
            "seo_title": ((working_after or {}).get("seo") or {}).get("title", ""),
            "seo_desc": ((working_after or {}).get("seo") or {}).get("description", ""),
            "sections_ui": sections_ui,
            "schema_ui_json": schema_ui_json,
            "error": None,
            "ok_message": "Changes saved.",
            "__entry_data_json": json.dumps(entry_json_for_client or {}, ensure_ascii=False),
            **_upload_context(active),
        },
    )


@router.post("/admin/pages/{entry_id}/popup-submissions/{submission_id}/delete")
def owa_popup_submission_delete(
    entry_id: int,
    submission_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        return RedirectResponse(url="/admin/projects", status_code=302)

    entry, section = _load_entry_or_404(db, entry_id, tid)
    if section.key != "pop_up":
        raise HTTPException(status_code=404, detail="Page not found")

    row = db.scalar(
        select(OwaPopupSubmission).where(
            and_(
                OwaPopupSubmission.id == submission_id,
                OwaPopupSubmission.tenant_id == tid,
            )
        )
    )
    if row:
        db.delete(row)
        db.commit()

    return RedirectResponse(url=f"/admin/pages/{entry_id}/edit", status_code=302)


# --------------------------- Admin Uploads (session-based) ---------------------------
@router.post("/admin/uploads")
def admin_upload_media(
    request: Request,
    file: UploadFile = File(...),
    field: str = Form(""),
    kind: str = Form("image"),
    entry_id: Optional[int] = Form(None),
    section_key: Optional[str] = Form(None),
):
    _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        raise HTTPException(status_code=400, detail="No active project.")

    if not _uploads_enabled_for_tenant(active):
        raise HTTPException(status_code=503, detail="Uploads not configured for this project.")

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    media_kind = (kind or "image").strip().lower()
    if media_kind not in ("image", "video"):
        raise HTTPException(status_code=400, detail="Invalid media kind.")

    content_type = (file.content_type or "").lower()
    if media_kind == "image" and not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Only images are allowed.")
    if media_kind == "video" and not content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Only videos are allowed.")

    size_bytes = None
    max_mb = int(getattr(settings, "UPLOAD_MAX_MB", 0) or 0)
    if max_mb > 0:
        try:
            file.file.seek(0, os.SEEK_END)
            size_bytes = file.file.tell()
            file.file.seek(0)
        except Exception:
            size_bytes = None
        if size_bytes is not None and size_bytes > (max_mb * 1024 * 1024):
            raise HTTPException(status_code=413, detail=f"File too large. Max {max_mb}MB.")

    tenant_folder = _safe_segment(_upload_tenant_folder(active, tid), "tenant")
    parts = ["uploads", tenant_folder]
    if section_key:
        parts.append(_safe_segment(section_key, "section"))
    if entry_id:
        parts.append(f"entry-{int(entry_id)}")
    if field:
        parts.append(_safe_segment(field, "field"))

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext and not re.match(r"^\.[a-z0-9]{1,6}$", ext):
        ext = ""

    unique = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    dest_path = "/".join(parts + [unique + ext])

    try:
        url = upload_file_to_firebase(file.file, content_type, dest_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Upload failed.") from exc

    return JSONResponse(
        {
            "url": url,
            "path": dest_path,
            "content_type": content_type,
            "size": size_bytes,
        }
    )


# --------------------------- Admin Publish proxy (session-based) ---------------------------
@router.post("/admin/pages/{entry_id}/publish")
def admin_publish_page(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Publish page: if data.__draft exists, promote it to root; otherwise publish current root.
    Delivery always reads entry.data (without __draft).
    """
    _require_web_user(request)
    active = _get_active_tenant(request)
    tid = int((active or {}).get("id") or 0)
    if not tid:
        raise HTTPException(status_code=400, detail="No active project.")

    entry, section = _load_entry_or_404(db, entry_id, tid)

    data_now = entry.data or {}
    working = data_now.get("__draft") if isinstance(data_now.get("__draft"), dict) else None
    candidate = (working or data_now)

    # Projects: publish merged view (root + draft) so we don't lose published items
    if getattr(section, "key", "") == "projects":
        candidate = _render_projects_data(data_now)
    # Home: publish merged view to keep featured projects and other blocks visible
    elif getattr(section, "key", "") == "home":
        candidate = _render_home_data(data_now)

    # Clean project lists (DEWA keys only) before publish to avoid resurrecting blank items
    candidate = _sanitize_dewa_projects_payload(candidate)

    # Allow publish if either sections[] has content OR object-style has meaningful blocks
    has_sections = isinstance(candidate.get("sections"), list) and len(candidate["sections"]) > 0
    object_keys = [k for k in candidate.keys() if k not in ("seo", "replace", "__draft")]
    has_object_blocks = any(isinstance(candidate.get(k), dict) for k in object_keys)
    has_array_blocks = any(isinstance(candidate.get(k), list) and len(candidate.get(k) or []) > 0 for k in object_keys)
    has_primitive_content = any(
        isinstance(candidate.get(k), (str, int, float, bool))
        for k in candidate.keys()
        if k not in ("seo", "replace", "__draft")
    )
    if not (has_sections or has_object_blocks or has_array_blocks or has_primitive_content):
        raise HTTPException(status_code=409, detail="Cannot publish an empty page. Save content first.")

    now = datetime.now(timezone.utc)

    # If draft exists, promote it and clear __draft
    if working is not None:
        published_at_prev = getattr(entry, "published_at", None)
        data_new = dict(candidate)
        data_new.pop("__draft", None)
        entry.data = data_new
        if published_at_prev:
            try:
                setattr(entry, "published_at", published_at_prev)
            except Exception:
                pass

    # Publish
    entry.status = "published"
    try:
        setattr(entry, "published_at", now if not getattr(entry, "published_at", None) else getattr(entry, "published_at"))
    except Exception:
        pass
    entry.updated_at = now

    db.add(entry)
    db.commit()
    db.refresh(entry)

    return JSONResponse({"ok": True, "status": "published", "entry_id": int(entry.id)})


# --------------------------- Sections JSON (for Admin UI helpers) ---------------------------
@router.get("/admin/tenants/{tenant_id}/sections.json")
def sections_json(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Minimal JSON list of sections for a tenant. Used by admin UI dropdowns/filters.
    """
    _require_web_user(request)

    rows = db.execute(
        select(Section.id, Section.key, Section.name)
        .where(Section.tenant_id == tenant_id)
        .order_by(Section.name.asc())
    ).all()

    data = [{"id": int(i), "key": k, "name": n} for (i, k, n) in rows]
    return JSONResponse({"sections": data})
