from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema


def _default_popup_text() -> dict:
    return {
        "title": "Pop-Up Text",
        "initialState": {
            "headline": "MODERN TRAINING GROUND\nFOR BEING HUMAN",
            "description": (
                "NOTHING EXTRA. NOTHING SUPERFICIAL.\n"
                "JUST SCIENCE-DRIVEN WELLBEING, COMMUNITY,\n"
                "AND PRACTICE. JOIN TO RECEIVE UPDATES AND\n"
                "MEMBER BENEFITS."
            ),
            "disclaimer": (
                "By submitting this form, you consent to receive marketing messages from OWA.\n"
                "Unsubscribe at any time. Privacy Policy & Terms."
            ),
            "backgroundImage": {"url": ""},
        },
        "successState": {
            "headline": "YOU'RE IN.",
            "description": "Welcome to the community.\nExpect something worth opening.",
            "backgroundImage": {"url": ""},
        },
    }


def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        out = deepcopy(base)
        for key, value in override.items():
            out[key] = _deep_merge(out.get(key), value)
        return out
    if isinstance(base, list) and isinstance(override, list):
        return deepcopy(override)
    return deepcopy(override) if override is not None else deepcopy(base)


def _has_popup_text_payload(data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("initialState"), dict) or isinstance(data.get("successState"), dict):
        return True
    draft = data.get("__draft")
    return isinstance(draft, dict) and (
        isinstance(draft.get("initialState"), dict) or isinstance(draft.get("successState"), dict)
    )


def _extract_popup_text_payload(source: dict | None) -> dict:
    defaults = _default_popup_text()
    if not isinstance(source, dict):
        return defaults

    root = deepcopy(source)
    root.pop("__draft", None)
    merged = _deep_merge(
        defaults,
        {
            "title": "Pop-Up Text",
            "initialState": root.get("initialState") or {},
            "successState": root.get("successState") or {},
        },
    )

    draft = source.get("__draft")
    if isinstance(draft, dict):
        merged["__draft"] = _deep_merge(
            defaults,
            {
                "title": "Pop-Up Text",
                "initialState": draft.get("initialState") or {},
                "successState": draft.get("successState") or {},
            },
        )
    return merged


def _analytics_payload() -> dict:
    return {
        "title": "Analytics",
        "notes": "Read-only analytics view for OWA pop-up submissions.",
    }


def _active_version(db, tenant_id: int, section_id: int) -> int:
    active = db.scalar(
        select(SectionSchema.version).where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
            SectionSchema.is_active.is_(True),
        )
    )
    if active is not None:
        return int(active)
    latest = db.scalar(
        select(SectionSchema.version)
        .where(
            SectionSchema.tenant_id == tenant_id,
            SectionSchema.section_id == section_id,
        )
        .order_by(SectionSchema.version.desc())
    )
    if latest is None:
        raise RuntimeError(f"No schema versions found for section_id={section_id}")
    return int(latest)


def _get_entry(db, tenant_id: int, section_id: int, slug: str) -> Entry | None:
    return db.scalar(
        select(Entry).where(
            Entry.tenant_id == tenant_id,
            Entry.section_id == section_id,
            Entry.slug == slug,
        )
    )


def main() -> None:
    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "owa"))
        if not tenant:
            raise RuntimeError("OWA tenant not found.")

        popup_section = db.scalar(
            select(Section).where(Section.tenant_id == tenant.id, Section.key == "pop_up")
        )
        popup_text_section = db.scalar(
            select(Section).where(Section.tenant_id == tenant.id, Section.key == "pop_up_text")
        )
        if not popup_section or not popup_text_section:
            raise RuntimeError("OWA pop_up or pop_up_text section not found. Seed schemas first.")

        popup_section.name = "Pop-Up"
        popup_text_section.name = "Pop-Up Text"

        popup_entry = _get_entry(db, tenant.id, popup_section.id, "pop_up")
        popup_text_entry = _get_entry(db, tenant.id, popup_text_section.id, "pop_up_text")

        source_data = None
        if popup_entry and _has_popup_text_payload(popup_entry.data):
            source_data = popup_entry.data
        elif popup_text_entry and _has_popup_text_payload(popup_text_entry.data):
            source_data = popup_text_entry.data

        popup_text_data = _extract_popup_text_payload(source_data)

        if popup_text_entry is None:
            popup_text_entry = Entry(
                tenant_id=tenant.id,
                section_id=popup_text_section.id,
                slug="pop_up_text",
                schema_version=_active_version(db, tenant.id, popup_text_section.id),
                status=(popup_entry.status if popup_entry else "draft"),
                data=popup_text_data,
                published_at=(popup_entry.published_at if popup_entry else None),
                archived_at=(popup_entry.archived_at if popup_entry else None),
            )
            db.add(popup_text_entry)
        else:
            popup_text_entry.schema_version = _active_version(db, tenant.id, popup_text_section.id)
            popup_text_entry.data = popup_text_data
            if popup_entry:
                popup_text_entry.status = popup_entry.status
                popup_text_entry.published_at = popup_entry.published_at
                popup_text_entry.archived_at = popup_entry.archived_at

        if popup_entry is None:
            popup_entry = Entry(
                tenant_id=tenant.id,
                section_id=popup_section.id,
                slug="pop_up",
                schema_version=_active_version(db, tenant.id, popup_section.id),
                status="draft",
                data=_analytics_payload(),
            )
            db.add(popup_entry)
        else:
            popup_entry.schema_version = _active_version(db, tenant.id, popup_section.id)
            popup_entry.data = _analytics_payload()

        db.commit()
        print(
            f"[OK] OWA popup migrated. analytics_entry_id={getattr(popup_entry, 'id', None)} "
            f"popup_text_entry_id={getattr(popup_text_entry, 'id', None)}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
