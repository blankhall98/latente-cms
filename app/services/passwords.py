# app/services/passwords.py
from passlib.context import CryptContext

pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
    bcrypt__truncate_error=False,
)

def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    return pwd_ctx.verify(plain, hashed)

def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain)
