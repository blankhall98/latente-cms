from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry, Section


_SCROLL_SECTION_KEYS = (
    "firstScrollSection",
    "secondScrollSection",
    "thirdScrollSection",
)


def _default_external_link() -> dict:
    return {
        "linkText": {"en": "", "es": ""},
        "linkUrl": "",
    }


def _ensure_external_links(data: dict) -> dict:
    cleaned = deepcopy(data or {})
    scroll_sections = cleaned.get("scrollSections")
    if isinstance(scroll_sections, dict):
        for key in _SCROLL_SECTION_KEYS:
            section = scroll_sections.get(key)
            if isinstance(section, dict):
                section.setdefault("externalLink", _default_external_link())

    draft = cleaned.get("__draft")
    if isinstance(draft, dict):
        draft_scroll_sections = draft.get("scrollSections")
        if isinstance(draft_scroll_sections, dict):
            for key in _SCROLL_SECTION_KEYS:
                section = draft_scroll_sections.get(key)
                if isinstance(section, dict):
                    section.setdefault("externalLink", _default_external_link())

    return cleaned


def run() -> None:
    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "dewa"))
        if not tenant:
            raise RuntimeError("DEWA tenant not found")

        section = db.scalar(
            select(Section).where(
                Section.tenant_id == int(tenant.id),
                Section.key == "dewa_capital",
            )
        )
        if not section:
            raise RuntimeError("DEWA Capital section not found")

        entry = db.scalar(
            select(Entry).where(
                Entry.tenant_id == int(tenant.id),
                Entry.section_id == int(section.id),
                Entry.slug == "dewa_capital",
            )
        )
        if not entry:
            raise RuntimeError("DEWA Capital entry not found")

        entry.data = _ensure_external_links(entry.data if isinstance(entry.data, dict) else {})
        entry.schema_version = 3
        entry.updated_at = datetime.now(timezone.utc)
        db.add(entry)
        db.commit()
        print("[OK] Migrated DEWA Capital entry to schema v3 and added scroll section external links.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
