# app/api/v1/endpoints/users.py
from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.auth import User
from app.schemas.admin import UserCreate, UserUpdate, UserOut
from app.deps.auth import get_current_user
from app.services.passwords import get_password_hash

router = APIRouter(prefix="/users", tags=["users"])


def _ensure_superadmin(current_user: User):
    if not getattr(current_user, "is_superadmin", False):
        raise HTTPException(status_code=403, detail="Superadmin only")


@router.get("", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _ensure_superadmin(current_user)

    stmt = select(User).order_by(User.id.asc())
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(User.email.ilike(term))

    users = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return users


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)

    email = payload.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=400, detail="Email already exists")

    u = User(
        email=email,
        full_name=payload.full_name,
        is_active=payload.is_active,
        is_superadmin=bool(payload.is_superadmin),
        hashed_password=get_password_hash(payload.password),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    patch: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_superadmin(current_user)
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    if patch.full_name is not None:
        u.full_name = patch.full_name
    if patch.is_active is not None:
        u.is_active = patch.is_active
    if patch.password:
        u.hashed_password = get_password_hash(patch.password)
    if patch.is_superadmin is not None:
        u.is_superadmin = bool(patch.is_superadmin)

    db.commit()
    db.refresh(u)
    return u

