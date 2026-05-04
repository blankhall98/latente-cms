from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry, Section


def _strip_logo_image(data: dict) -> dict:
    cleaned = deepcopy(data or {})
    bottom_hero = cleaned.get("bottomHero")
    if isinstance(bottom_hero, dict):
        bottom_hero.pop("logoImage", None)
    draft = cleaned.get("__draft")
    if isinstance(draft, dict):
        draft_bottom_hero = draft.get("bottomHero")
        if isinstance(draft_bottom_hero, dict):
            draft_bottom_hero.pop("logoImage", None)
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

        entry.data = _strip_logo_image(entry.data if isinstance(entry.data, dict) else {})
        entry.schema_version = 2
        entry.updated_at = datetime.now(timezone.utc)
        db.add(entry)
        db.commit()
        print("[OK] Migrated DEWA Capital entry to schema v2 and removed bottomHero.logoImage.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
