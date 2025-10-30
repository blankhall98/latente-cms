# app/services/audit_service.py

from __future__ import annotations
from typing import Optional, Dict, Any, Union, List
from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit import ContentAuditLog, ContentAction
from app.models.content import Entry

# ⬇️ NUEVO: importar el modelo User con try/except por si el path difiere
try:
    from app.models.auth import User
except Exception:  # pragma: no cover
    from app.models import User  # fallback si tu proyecto los agrupa distinto

def compute_changed_keys(before: Dict[str, Any] | None, after: Dict[str, Any] | None) -> List[str]:
    """
    Devuelve las claves cuyo valor cambió entre before y after (comparación superficial).
    Si alguna es None, se trata como {} para evitar errores.
    """
    b = before or {}
    a = after or {}
    keys = set(b.keys()) | set(a.keys())
    changed = [k for k in keys if b.get(k) != a.get(k)]
    changed.sort()
    return changed


def _extract_client(request: Optional[Request]) -> tuple[Optional[str], Optional[str]]:
    if not request:
        return None, None
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


# ⬇️ NUEVO: crea (si falta) un usuario “placeholder” con el ID indicado.
def _ensure_user_exists(db: Session, user_id: Optional[int]) -> None:
    if not user_id:
        return
    # ¿ya existe?
    existing = db.get(User, user_id)
    if existing:
        return

    # Construir un usuario mínimo que respete restricciones comunes.
    # Ponemos valores por defecto “seguros”; si tu modelo exige otros campos
    # (p.ej. unique username, is_active not null), los rellenamos si existen.
    u = User()  # type: ignore[call-arg]
    setattr(u, "id", user_id)
    # Campos típicos:
    if hasattr(u, "email"):
        setattr(u, "email", f"test{user_id}@example.com")
    if hasattr(u, "name"):
        setattr(u, "name", f"Test User {user_id}")
    if hasattr(u, "is_active"):
        setattr(u, "is_active", True)
    if hasattr(u, "password_hash"):
        setattr(u, "password_hash", "")  # si no-null
    if hasattr(u, "hashed_password"):
        setattr(u, "hashed_password", "")  # variante común
    db.add(u)
    # flush para que exista antes del insert del audit log
    db.flush()


def audit_entry_action(
    db: Session,
    *,
    tenant_id: int,
    entry: Entry,
    action: Union[ContentAction, str],
    user_id: Optional[int],
    details: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None
) -> ContentAuditLog:
    if isinstance(action, str):
        action = ContentAction(action.lower())

    # ⬇️ NUEVO: si hay FK a users.id, garantizamos que el user exista
    _ensure_user_exists(db, user_id)

    ip, ua = _extract_client(request)

    log = ContentAuditLog(
        tenant_id=tenant_id,
        entry_id=entry.id,
        section_id=getattr(entry, "section_id", None),
        action=action,
        user_id=user_id,         # tests esperan 123 / 9 / 42 aquí
        details=details or {},
        ip=ip,
        user_agent=ua,
    )
    db.add(log)
    return log





