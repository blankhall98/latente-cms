from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant
from app.models.owa_popup import OwaPopupSubmission


router = APIRouter()


class OwaPopupSubmissionIn(BaseModel):
    email: EmailStr = Field(..., max_length=320)
    gender: str = Field(..., min_length=1, max_length=64)
    birth_date: date

    @field_validator("gender")
    @classmethod
    def _clean_gender(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("gender is required")
        return cleaned

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OwaPopupSubmissionIn":
        mapped = dict(payload or {})
        if "email" not in mapped:
            mapped["email"] = mapped.get("email_address") or mapped.get("emailAddress")
        if "birth_date" not in mapped:
            mapped["birth_date"] = mapped.get("birthDate")
        return cls.model_validate(mapped)


def _get_owa_tenant(db: Session) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "owa"))
    if not tenant:
        raise HTTPException(status_code=404, detail="OWA tenant not found")
    return tenant


@router.post("/owa/popup-submissions")
def submit_owa_popup_submission(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    try:
        data = OwaPopupSubmissionIn.from_payload(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}") from exc

    today = date.today()
    if data.birth_date > today:
        raise HTTPException(status_code=422, detail="birth_date cannot be in the future")

    tenant = _get_owa_tenant(db)

    submission = OwaPopupSubmission(
        tenant_id=int(tenant.id),
        email=str(data.email).strip().lower(),
        gender=data.gender.strip(),
        birth_date=data.birth_date,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    return {
        "ok": True,
        "id": int(submission.id),
        "endpoint": "POST /api/v1/owa/popup-submissions",
    }
