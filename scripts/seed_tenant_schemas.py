# scripts/seed_tenant_schemas.py
# Sembrador por tenant que carga esquemas desde /app/schemas/<tenant>/<section>/vX.json
from __future__ import annotations
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.seeds.content_loader import SectionFile, bulk_load_tenant_schemas

"""
Uso (PowerShell/Terminal):
  (.venv) PS> python -m scripts.seed_tenant_schemas owa
  (.venv) PS> python -m scripts.seed_tenant_schemas OWA

Estructura esperada:
app/schemas/
  owa/
    landing_pages/
      v1.json
      v2.json   <-- (nuevo, con x-ui hints)
"""

def run(tenant_key_or_name: str):
    files = [
        # OWA — LandingPages v1 (legacy/monolítico)
        SectionFile(
            section_key="LandingPages",
            section_name="Landing Pages",
            version=1,
            file_path="owa/landing_pages/v1.json",  # relativo a base_dir
            is_active=False,  # v1 queda disponible pero NO activo
        ),
        # OWA — LandingPages v2 (editor-friendly con x-ui:* hints)
        SectionFile(
            section_key="LandingPages",
            section_name="Landing Pages",
            version=2,
            file_path="owa/landing_pages/v2.json",  # relativo a base_dir
            is_active=True,  # v2 se activa
        ),
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
        print(f"[OK] Schemas (v1+v2) cargados y v2 activado para tenant='{tenant_key_or_name}'.")
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.seed_tenant_schemas <tenant_key_or_name>")
        raise SystemExit(1)
    run(sys.argv[1])


