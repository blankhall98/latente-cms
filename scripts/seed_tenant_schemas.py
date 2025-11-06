# scripts/seed_tenant_schemas.py
# Sembrador por tenant que carga esquemas desde /app/schemas/<tenant>/<section>/vX.json
from __future__ import annotations
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.seeds.content_loader import SectionFile, bulk_load_tenant_schemas

"""
Uso (PowerShell/Terminal):
  (.venv) PS> python -m scripts.seed_tenant_schemas owa
"""

def run(tenant_key_or_name: str):
    files = [
        # OWA — LandingPages v1 (content-only v1)
        SectionFile(
            section_key="LandingPages",
            section_name="Landing Pages",
            version=1,
            file_path="owa/landing_pages/v1.json",  # relativo a base_dir
            is_active=True,  # activamos v1 en esta fase
        ),
        # Si luego quieres cargar v2, agrega otra SectionFile con is_active=False por ahora.
        # SectionFile(section_key="LandingPages", section_name="Landing Pages",
        #             version=2, file_path="owa/landing_pages/v2.json", is_active=False),
    ]

    base_dir = "app/schemas"

    db: Session = SessionLocal()
    try:
        bulk_load_tenant_schemas(
            db,
            tenant_key_or_name=tenant_key_or_name,
            base_dir=base_dir,
            files=files,
        )
        db.commit()  # <-- commit del batch aquí
        print(f"[OK] Schemas cargados para tenant='{tenant_key_or_name}'. v1 activo.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.seed_tenant_schemas <tenant_key_or_name>")
        raise SystemExit(1)
    run(sys.argv[1])


