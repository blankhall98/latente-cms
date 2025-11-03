# app/services/passwords.py
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], bcrypt__truncate_error=False, deprecated="auto")

def hash_password(plain: str) -> str:
    return _pwd.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    return _pwd.verify(plain, hashed)

