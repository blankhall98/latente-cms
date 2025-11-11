# scripts/set_password.py
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import User
from app.services.passwords import hash_password

def run() -> None:
    db: Session = SessionLocal()
    try:
        pairs = [
            ("zero2hero@demo.com", "admin123"),
            ("latente@demo.com", "admin123"),
            ("hello@owawellness.com", "owa123"),
        ]
        for email, plain in pairs:
            u = db.scalar(select(User).where(User.email == email))
            if u:
                u.hashed_password = hash_password(plain)
                print(f"[OK] Set password for {email}")
            else:
                print(f"[SKIP] User not found: {email}")
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    run()

