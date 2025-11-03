# app/services/passwords.py
from __future__ import annotations

from passlib.context import CryptContext

# Keep bcrypt settings consistent with your seeds/fixtures
_pwd = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__truncate_error=False,
)

def hash_password(plain: str) -> str:
    """Hash a plaintext password (primary internal name)."""
    return _pwd.hash(plain)

# Backward-compatible alias used by some endpoints/seeds:
def get_password_hash(plain: str) -> str:
    """Alias for compatibility with code importing get_password_hash."""
    return hash_password(plain)

def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    if not hashed:
        return False
    return _pwd.verify(plain, hashed)


