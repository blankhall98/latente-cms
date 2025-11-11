# app/scripts/seed_tenant_schemas.py
# Carga todas las secciones/esquemas encontrados bajo app/schemas/<tenant>/<section>/vX.json
# y activa por defecto la versión más alta por sección. Permite overrides de activación.

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Tuple

# Ensure "app" is importable when run as a script
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.seeds.content_loader import SectionFile, bulk_load_tenant_schemas  # noqa: E402


_VERSION_RX = re.compile(r"v?(\d+)\.json$", re.IGNORECASE)


def _parse_version_from_filename(p: Path) -> int | None:
    m = _VERSION_RX.search(p.name)
    return int(m.group(1)) if m else None


def _pretty_section_name(section_key: str) -> str:
    # "landing_pages" -> "Landing Pages", "home" -> "Home"
    return section_key.replace("_", " ").strip().title()


def _discover_section_files(base_dir: Path, tenant: str) -> List[SectionFile]:
    """
    Explora app/schemas/<tenant>/<section>/vX.json y arma la lista de SectionFile.
    Por sección, marca is_active=True solo para la versión más alta encontrada.
    """
    tenant_dir = base_dir / tenant
    if not tenant_dir.exists():
        raise SystemExit(f"[ERR] No existe carpeta de schemas para tenant '{tenant}': {tenant_dir}")

    # Map: section_key -> List[(version, Path)]
    found: Dict[str, List[Tuple[int, Path]]] = {}
    for section_dir in sorted([p for p in tenant_dir.iterdir() if p.is_dir()]):
        section_key = section_dir.name
        for f in section_dir.glob("*.json"):
            v = _parse_version_from_filename(f)
            if v is None:
                continue
            found.setdefault(section_key, []).append((v, f))

    if not found:
        raise SystemExit(f"[ERR] No se encontraron archivos vX.json bajo '{tenant_dir}/<section>/'.")

    files: List[SectionFile] = []
    for section_key, versions in found.items():
        versions.sort(key=lambda x: x[0])  # ascending
        max_version = versions[-1][0]
        section_name = _pretty_section_name(section_key)
        for (v, fpath) in versions:
            rel_path = fpath.relative_to(base_dir).as_posix()  # e.g. "anro/home/v1.json"
            files.append(
                SectionFile(
                    section_key=section_key,
                    section_name=section_name,
                    version=v,
                    file_path=rel_path,
                    is_active=(v == max_version),  # activate newest by default
                )
            )
    return files


def _apply_active_overrides(files: List[SectionFile], overrides: List[str]) -> None:
    """
    Permite forzar activaciones específicas: --set-active home=1 portfolio=2
    Desactiva otras versiones de esa sección.
    """
    if not overrides:
        return
    # Normalize into dict: section_key -> version
    wanted: Dict[str, int] = {}
    for item in overrides:
        if "=" not in item:
            raise SystemExit(f"[ERR] Formato inválido en --set-active: '{item}' (usa section=version)")
        k, v = item.split("=", 1)
        k = k.strip()
        try:
            ver = int(v.strip())
        except ValueError:
            raise SystemExit(f"[ERR] Versión inválida en --set-active: '{item}'")
        wanted[k] = ver

    # Turn off all, then re-enable target
    for k, ver in wanted.items():
        any_found = False
        for sf in files:
            if sf.section_key == k:
                any_found = True
                sf.is_active = (sf.version == ver)
        if not any_found:
            print(f"[WARN] --set-active ignorado: sección '{k}' no encontrada en archivos descubiertos")


def run(
    tenant_key_or_name: str,
    base_dir: str = "app/schemas",
    set_active: List[str] | None = None,
    dry_run: bool = False,
) -> None:
    base = Path(base_dir)
    files = _discover_section_files(base, tenant_key_or_name)
    _apply_active_overrides(files, set_active or [])

    print(f"[INFO] Tenant='{tenant_key_or_name}'  BaseDir='{base.as_posix()}'")
    for sf in files:
        mark = " (ACTIVE)" if sf.is_active else ""
        print(f"  - {sf.section_key}@v{sf.version}: {sf.file_path}{mark}")

    if dry_run:
        print("[DRY-RUN] No se realizaron cambios.")
        return

    db: Session = SessionLocal()
    try:
        bulk_load_tenant_schemas(
            db,
            tenant_key_or_name=tenant_key_or_name,
            base_dir=base.as_posix(),
            files=files,
        )
        db.commit()
        print("[OK] Schemas cargados/actualizados correctamente.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(
        description="Carga esquemas JSON de un tenant desde app/schemas/<tenant>/<section>/vX.json"
    )
    ap.add_argument("tenant", help="slug o nombre del tenant (ej. anro, owa)")
    ap.add_argument(
        "--base-dir", default="app/schemas",
        help="directorio base donde viven los schemas (default: app/schemas)"
    )
    ap.add_argument(
        "--set-active", nargs="*", default=[],
        help="overrides para activar una versión específica por sección, ej: --set-active home=1 portfolio=2"
    )
    ap.add_argument("--dry-run", action="store_true", help="solo mostrar, no aplicar cambios")
    args = ap.parse_args()

    run(
        tenant_key_or_name=args.tenant,
        base_dir=args.base_dir,
        set_active=args.set_active,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()



