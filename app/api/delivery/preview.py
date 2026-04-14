# app/api/delivery/preview.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.content import Entry
from app.security.preview_tokens import verify_preview_token

router = APIRouter(prefix="/delivery/v1", tags=["delivery"])

@router.get("/preview")
def preview_via_token(
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    data = verify_preview_token(token)
    tenant_id = int(data["tenant_id"])
    entry_id = int(data["entry_id"])

    e = db.get(Entry, entry_id)
    if not e or e.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Preview not found")

    # Preview responses must never be cached — content is unpublished/draft.
    return JSONResponse(
        content={
            "tenant_id": e.tenant_id,
            "section_id": e.section_id,
            "slug": e.slug,
            "schema_version": data.get("schema_version", e.schema_version),
            "status": e.status,
            "data": e.data or {},
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        },
        headers={"Cache-Control": "no-store"},
    )
