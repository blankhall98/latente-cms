# app/api/deps/auth.py
# ── Dependencias de autenticación/autorización para FastAPI
from __future__ import annotations
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.authz import user_has_permission

# Nota: En producción conecta esto con tu JWT real (e.g., get_current_user()).
# Por ahora tomamos el user_id de un header para simplificar las pruebas:
#   X-User-Id: <int>
def get_current_user_id(x_user_id: int | None = Header(default=None, alias="X-User-Id")) -> int:
    if x_user_id is None:
        # 401 si no hay identidad (luego se reemplaza por JWT)
        raise HTTPException(status_code=401, detail="Missing X-User-Id (replace with real JWT auth)")
    return x_user_id

def require_permission(perm_key: str):
    """
    Crea una dependencia que exige `perm_key` para el tenant indicado.
    La dependencia requiere que el endpoint exponga `tenant_id` como query param
    o que lo pase explícitamente a esta verificación.
    """
    def _dep(
        tenant_id: int = None,  # FastAPI inyecta desde query param si existe
        user_id: int = Depends(get_current_user_id),
        db: Session = Depends(get_db),
    ):
        if tenant_id is None:
            # Si el endpoint no tiene tenant_id como query, se debe verificar dentro del handler
            raise HTTPException(status_code=400, detail="tenant_id is required for permission check")
        if not user_has_permission(db, user_id=user_id, tenant_id=tenant_id, perm_key=perm_key):
            raise HTTPException(status_code=403, detail=f"Missing permission: {perm_key}")
        return True
    return _dep
