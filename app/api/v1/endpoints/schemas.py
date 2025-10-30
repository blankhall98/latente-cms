# app/api/v1/endpoints/schemas.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps.auth import require_permission, get_current_user_id
from app.services.ui_schema_service import build_ui_contract

router = APIRouter(prefix="/schemas", tags=["schemas"])

@router.get("/{section_id}/active/ui")
def get_active_ui_schema(
    section_id: int,
    tenant_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    _: None = Depends(require_permission("cms.schemas.read")),
):
    try:
        contract = build_ui_contract(db, tenant_id=tenant_id, section_id=section_id)
        return contract
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
