# scripts/seed_owa_v3.py
from __future__ import annotations
import json
from pathlib import Path

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import Tenant
from app.models.content import Entry
from app.schemas.content import EntryCreate, EntryUpdate
from app.services.content_service import create_section, add_schema_version

SCHEMA_PATH = Path("app/schemas/owa/landing_pages/v3.json")
CONTENT_PATH = Path("content/owa/home_v3.json")


def _get_tenant(db: Session, slug: str = "owa") -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not t:
        raise RuntimeError(
            f"Tenant slug='{slug}' no encontrado. Crea el tenant primero (scripts/create_tenant.py)."
        )
    return t


def _validate_payload(schema: dict, payload: dict, label: str = "v3"):
    try:
        from jsonschema import validate, Draft202012Validator
        Draft202012Validator.check_schema(schema)
        validate(instance=payload, schema=schema, cls=Draft202012Validator)
    except Exception as e:
        raise RuntimeError(f"❌ Payload no valida contra el schema {label}: {e}") from e


def run():
    db: Session = SessionLocal()
    try:
        # --- Tenant OWA ---
        tenant = _get_tenant(db, slug="owa")

        # --- Sección (idempotente) ---
        section = create_section(
            db,
            tenant_id=tenant.id,
            key="LandingPages",
            name="Landing Pages",
            description="Página de inicio OWA (layout design-first v3)",
        )
        db.flush()

        # --- Cargar schema v3 ---
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"No existe schema: {SCHEMA_PATH.as_posix()}")
        schema_v3 = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        # --- Cargar contenido v3 ---
        if not CONTENT_PATH.exists():
            raise FileNotFoundError(f"No existe contenido: {CONTENT_PATH.as_posix()}")
        content_payload = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))

        # --- Validación estricta contra v3 ---
        _validate_payload(schema_v3, content_payload, label="v3")

        # --- Registrar schema v3 sin activarlo (evita UniqueViolation si ya hay uno activo) ---
        add_schema_version(
            db,
            tenant_id=tenant.id,
            section_id=section.id,
            version=3,
            title="Landing v3 (design-first)",
            schema=schema_v3,
            is_active=False,  # no activamos aquí
        )

        # --- Upsert del Entry 'home' con schema_version=3 ---
        existing = db.scalar(
            select(Entry).where(
                and_(
                    Entry.tenant_id == tenant.id,
                    Entry.section_id == section.id,
                    Entry.slug == "home",
                )
            )
        )

        if not existing:
            payload = EntryCreate(
                tenant_id=tenant.id,
                section_id=section.id,
                slug="home",
                schema_version=3,
                status="draft",
                data=content_payload,
            )
            entry = _safe_create_entry(db, payload)
            print(f"[OK] Creado entry draft slug='home' id={entry.id} (schema_version=3)")
        else:
            payload = EntryUpdate(
                tenant_id=tenant.id,
                section_id=section.id,
                data=content_payload,
                schema_version=3,
            )
            entry = _safe_update_entry(db, existing.id, payload)
            print(f"[OK] Actualizado entry slug='home' id={entry.id} a schema_version=3")

        db.commit()
        print("[OK] OWA v3: schema registrado (inactivo) + contenido v3 cargado en 'home' (draft)")
        print("👉 Si deseas ACTIVAR v3 como schema activo, usa el endpoint de activar versión o avísame y te doy un snippet.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --- wrappers finos para evitar import circular y controlar flags ---
def _safe_create_entry(db: Session, payload: EntryCreate) -> Entry:
    from app.services.content_service import create_entry
    return create_entry(db, payload)

def _safe_update_entry(db: Session, entry_id: int, payload: EntryUpdate) -> Entry:
    """
    Llama update_entry pasando tenant_id para que _get_entry_or_404 valide correctamente.
    Si la firma no acepta tenant_id, reintenta sin él.
    Si aún así el servicio lanza ValueError('Entry not found.'), hacemos update manual.
    """
    from app.services.content_service import update_entry as svc_update
    try:
        # Preferente: con tenant_id para cumplir la validación interna
        return svc_update(db, entry_id, payload, patch=False, tenant_id=payload.tenant_id)
    except TypeError:
        # Compat: firmas antiguas sin tenant_id
        try:
            return svc_update(db, entry_id, payload, patch=False)
        except ValueError as e:
            if str(e).lower().startswith("entry not found"):
                # Fallback: update manual controlado
                eobj = db.get(Entry, entry_id)
                if not eobj:
                    raise
                eobj.data = payload.data
                eobj.schema_version = payload.schema_version
                # mantenemos status tal cual
                db.flush()
                return eobj
            raise
    except ValueError as e:
        if str(e).lower().startswith("entry not found"):
            eobj = db.get(Entry, entry_id)
            if not eobj:
                raise
            eobj.data = payload.data
            eobj.schema_version = payload.schema_version
            db.flush()
            return eobj
        raise


if __name__ == "__main__":
    run()




