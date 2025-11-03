# app/api/v1/endpoints/tenants.py
from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Tenant
from app.schemas.admin import TenantCreate, TenantUpdate, TenantOut
from app.deps.auth import get_current_user, User

router = APIRouter(prefix="/tenants", tags=["tenants"])

def _ensure_superadmin(current_user: User):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")

@router.get("", response_model=List[TenantOut])
def list_tenants(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _ensure_superadmin(current_user)
    stmt = select(Tenant).order_by(Tenant.id.asc())
    if q:
        stmt = stmt.where(Tenant.name.ilike(f"%{q}%"))
    tenants = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return tenants

@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t

@router.post("", response_model=TenantOut, status_code=201)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)
    if db.scalar(select(Tenant).where(Tenant.slug == payload.slug)):
        raise HTTPException(status_code=400, detail="Slug already exists")
    t = Tenant(name=payload.name, slug=payload.slug)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t

@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: int,
    patch: TenantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if patch.name is not None:
        t.name = patch.name
    if patch.slug is not None:
        # opcional: validar unicidad
        existing = db.scalar(select(Tenant).where(Tenant.slug == patch.slug, Tenant.id != tenant_id))
        if existing:
            raise HTTPException(status_code=400, detail="Slug already in use")
        t.slug = patch.slug

    db.commit()
    db.refresh(t)
    return t
