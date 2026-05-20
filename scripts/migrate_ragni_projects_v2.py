"""Remove the retired Ragni project typology field from stored content."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry, Section, SectionSchema


def _strip_typology(payload: object) -> int:
    if not isinstance(payload, dict):
        return 0
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return 0

    changed = 0
    for project in projects:
        if isinstance(project, dict) and "typology" in project:
            project.pop("typology", None)
            changed += 1
    return changed


def run() -> None:
    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == "ragni-grady"))
        if not tenant:
            print("SKIP: ragni-grady tenant not found")
            return

        section = db.scalar(
            select(Section).where(
                Section.tenant_id == tenant.id,
                Section.key == "projects",
            )
        )
        if not section:
            print("SKIP: ragni-grady projects section not found")
            return

        entry = db.scalar(
            select(Entry).where(
                Entry.tenant_id == tenant.id,
                Entry.section_id == section.id,
                Entry.slug == "projects",
            )
        )
        if not entry:
            print("SKIP: ragni-grady projects entry not found")
            return

        data = entry.data if isinstance(entry.data, dict) else {}
        changed = _strip_typology(data)
        draft = data.get("__draft")
        if isinstance(draft, dict):
            changed += _strip_typology(draft)

        active_schema = db.scalar(
            select(SectionSchema)
            .where(
                SectionSchema.section_id == section.id,
                SectionSchema.is_active.is_(True),
            )
            .order_by(SectionSchema.version.desc())
        )

        if changed:
            entry.data = data
            if active_schema:
                entry.schema_version = active_schema.version
            entry.updated_at = datetime.now(timezone.utc)
            db.add(entry)
            db.commit()

        print(f"removed_typology={changed}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
