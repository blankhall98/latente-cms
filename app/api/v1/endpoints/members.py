# app/api/v1/endpoints/members.py
from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import UserTenant, UserTenantStatus, User, Tenant, Role
from app.schemas.admin import MemberCreate, MemberUpdate, MemberOut
from app.deps.auth import get_current_user, user_has_permission

router = APIRouter(prefix="/members", tags=["members"])

def _ensure_can_manage_members(db: Session, current_user: User, tenant_id: int):
    # Superadmin siempre puede
    if current_user.is_superadmin:
        return
    # Si no, requiere permiso en el tenant
    if not user_has_permission(db, user_id=current_user.id, tenant_id=tenant_id, perm_key="org:members:manage"):
        raise HTTPException(status_code=403, detail="Missing permission: org:members:manage")

@router.get("", response_model=List[MemberOut])
def list_members(
    tenant_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_can_manage_members(db, current_user, tenant_id)
    qs = (
        select(UserTenant)
        .where(UserTenant.tenant_id == tenant_id)
        .order_by(UserTenant.id.asc())
    )
    members = db.execute(qs).scalars().all()
    return members

@router.post("", response_model=MemberOut, status_code=201)
def add_member(
    payload: MemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_can_manage_members(db, current_user, payload.tenant_id)

    # Validar existencia básica
    if not db.get(User, payload.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if not db.get(Tenant, payload.tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not db.get(Role, payload.role_id):
        raise HTTPException(status_code=404, detail="Role not found")

    # Unicidad user-tenant
    existing = db.scalar(
        select(UserTenant).where(
            and_(UserTenant.user_id == payload.user_id, UserTenant.tenant_id == payload.tenant_id)
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="User already linked to this tenant")

    ut = UserTenant(
        user_id=payload.user_id,
        tenant_id=payload.tenant_id,
        role_id=payload.role_id,
        status=UserTenantStatus(payload.status or "active"),
    )
    db.add(ut)
    db.commit()
    db.refresh(ut)
    return ut

@router.patch("/{member_id}", response_model=MemberOut)
def update_member(
    member_id: int,
    patch: MemberUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ut = db.get(UserTenant, member_id)
    if not ut:
        raise HTTPException(status_code=404, detail="Member not found")

    _ensure_can_manage_members(db, current_user, ut.tenant_id)

    if patch.role_id is not None:
        if not db.get(Role, patch.role_id):
            raise HTTPException(status_code=404, detail="Role not found")
        ut.role_id = patch.role_id
    if patch.status is not None:
        ut.status = UserTenantStatus(patch.status)

    db.commit()
    db.refresh(ut)
    return ut
