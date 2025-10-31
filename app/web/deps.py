# app/web/deps.py
from typing import Optional
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.session import get_db
from app.models.auth import User

def get_current_user_web(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    uid: Optional[int] = request.session.get("uid")
    if not uid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authenticated")
    user = db.scalar(select(User).where(User.id == uid))
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid session")
    return user
