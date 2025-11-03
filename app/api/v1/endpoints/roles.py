# app/api/v1/endpoints/roles.py
from __future__ import annotations
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import Role, Permission
from app.schemas.admin import RoleOut, PermissionOut
from app.deps.auth import get_current_user, User

router = APIRouter(prefix="/rbac", tags=["rbac"])

def _ensure_superadmin(current_user: User):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")

@router.get("/roles", response_model=List[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)
    return db.execute(select(Role).order_by(Role.id.asc())).scalars().all()

@router.get("/permissions", response_model=List[PermissionOut])
def list_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)
    return db.execute(select(Permission).order_by(Permission.id.asc())).scalars().all()
