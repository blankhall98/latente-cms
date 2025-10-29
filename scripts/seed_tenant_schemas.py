# scripts/seed_tenant_schemas.py
# Sembrador por tenant que usa el loader de archivos
from __future__ import annotations
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.seeds.content_loader import SectionFile, bulk_load_tenant_schemas

"""
Uso (PowerShell):
(.venv) PS> python -m scripts.seed_tenant_schemas owa
o también:
(.venv) PS> python -m scripts.seed_tenant_schemas OWA

Estructura esperada (según tu screenshot):
app/schemas/
  owa/
    landing_pages/
      v1.json
"""

def run(tenant_key_or_name: str):
    files = [
        # OWA — LandingPages v1 (monolítico)
        SectionFile(
            section_key="LandingPages",
            section_name="Landing Pages",
            version=1,
            file_path="owa/landing_pages/v1.json",  # relativo a base_dir
            is_active=True,
        ),
    ]
    # <- AJUSTE CLAVE: tu carpeta está bajo app/schemas
    base_dir = "app/schemas"

    db: Session = SessionLocal()
    try:
        bulk_load_tenant_schemas(
            db,
            tenant_key_or_name=tenant_key_or_name,
            base_dir=base_dir,
            files=files,
        )
        print(f"[OK] Schemas cargados para tenant='{tenant_key_or_name}'.")
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.seed_tenant_schemas <tenant_key_or_name>")
        raise SystemExit(1)
    run(sys.argv[1])

