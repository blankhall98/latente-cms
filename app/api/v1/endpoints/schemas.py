# app/api/v1/endpoints/schemas.py
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps.auth import require_permission, get_current_user_id
from app.services.ui_schema_service import build_ui_contract

router = APIRouter(prefix="/schemas", tags=["schemas"])

@router.get("/{section_id}/active/ui")
def get_active_ui_schema(
    section_id: int,
    tenant_id: int = Query(..., ge=1, description="Tenant ID al que pertenece la sección"),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    _: None = Depends(require_permission("cms.schemas.read")),
) -> Dict[str, Any]:
    """
    Devuelve el **contrato de UI** para la sección indicada, basado en el **JSON Schema activo**
    (por tenant + section). Este contrato ya viene aplanado/listo para autogenerar formularios
    en el Admin (autoform), e incluye metadatos útiles (version, title, ui_hints, etc.).

    Params:
      - tenant_id (query): ID del tenant (requerido)
      - section_id (path): ID de la sección (requerido)

    Seguridad:
      - Requiere permiso RBAC: `cms.schemas.read`
      - Usa el usuario autenticado `get_current_user_id` para el contexto

    Respuesta (ejemplo):
    {
      "tenant_id": 4,
      "section_id": 2,
      "version": 2,
      "title": "OWA Landing v2",
      "schema": { ... JSON Schema ... },
      "ui_hints": [
        {"kind":"scalar","name":"hero__title","type":"text","label":"Title","help":""},
        {"kind":"enum","name":"layout","label":"Layout","choices":["A","B","C"]},
        {"kind":"csv","name":"tags","label":"Tags"},
        ...
      ]
    }
    """
    try:
        contract = build_ui_contract(db, tenant_id=tenant_id, section_id=section_id)
        if not isinstance(contract, dict):
            # Defensa por contrato: siempre debe ser un dict serializable
            raise HTTPException(status_code=500, detail="UI contract inválido (tipo inesperado).")
        # (Opcional) podrías añadir aquí headers de cache si el contrato es estable.
        return contract
    except LookupError as e:
        # Lanzada por build_ui_contract cuando no hay schema activo o no existe sección/tenant
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        # Re-propaga HTTPException tal cual
        raise
    except Exception as e:
        # Falla inesperada (log recomendado en middleware)
        raise HTTPException(status_code=500, detail="No se pudo construir el UI contract.") from e
