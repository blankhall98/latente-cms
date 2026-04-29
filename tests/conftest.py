# tests/conftest.py
from __future__ import annotations

import pytest
import uuid
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.settings import settings
from app.db.session import get_db
from app.models.auth import Permission, Role, RolePermission, User, UserTenant
from app.security.jwt import create_access_token

# Engine a la misma BD definida en settings (las tablas deben existir vía Alembic)
engine = create_engine(settings.SQLALCHEMY_DATABASE_URL, future=True)

# Factory de sesiones para pruebas
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture(scope="function")
def db() -> Session:
    """
    Crea UNA sesión por prueba, aislada dentro de una transacción explícita.
    Al finalizar cada prueba, se hace rollback para dejar la BD limpia.
    Además, aplica una limpieza puntual para asegurar que 'author' NO tenga
    el permiso 'content:publish' (evita falsos positivos en tests RBAC).
    """
    connection = engine.connect()
    trans = connection.begin()
    session = TestingSessionLocal(bind=connection)

    # --- Limpieza "cinturón y tirantes" previa a cada test ---
    # Si existen los objetos/semillas, elimina cualquier asignación de publish al rol author
    try:
        session.execute(text("""
            DELETE FROM role_permissions rp
            USING roles r, permissions p
            WHERE rp.role_id = r.id
              AND rp.permission_id = p.id
              AND LOWER(r.key) IN ('author', 'tenant_author')
              AND p.key = 'content:publish';
        """))
        session.flush()
    except Exception:
        # Si aún no existen tablas/semillas, no rompas el fixture del test
        session.rollback()
        # reabre la transacción si hicimos rollback por error de metadatos
        trans = connection.begin()
        session = TestingSessionLocal(bind=connection)

    try:
        yield session
    finally:
        # Cierra sesión y revierte cualquier cambio de la prueba
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _override_get_db(db: Session):
    """
    Override automático de la dependencia get_db de FastAPI para que
    todos los endpoints usen **la misma sesión** de la prueba en curso.
    """
    from app.main import app  # import tardío para evitar ciclos
    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    try:
        yield
    finally:
        # Limpia el override para la siguiente prueba
        app.dependency_overrides.pop(get_db, None)

# Alias para compatibilidad con tests que esperan 'db_session'
@pytest.fixture
def db_session(db):
    return db


def _ensure_user(
    db: Session,
    *,
    user_id: int | None = None,
    is_superadmin: bool = False,
) -> User:
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        suffix = str(user_id) if user_id is not None else "auto"
        user = User(
            email=f"test-{suffix}-{id(db)}@example.com",
            hashed_password="test",
            is_active=True,
            is_superadmin=is_superadmin,
        )
        if user_id is not None:
            user.id = int(user_id)
        db.add(user)
    else:
        user.is_active = True
        user.is_superadmin = is_superadmin
    db.flush()
    return user


def _ensure_permission(db: Session, key: str) -> Permission:
    perm = db.query(Permission).filter(Permission.key == key).first()
    if perm is None:
        perm = Permission(key=key, description=key)
        db.add(perm)
        db.flush()
    return perm


def _ensure_role_with_permissions(db: Session, permission_keys: tuple[str, ...]) -> Role:
    role = Role(
        key=f"test_role_{uuid.uuid4().hex[:12]}",
        label="Test Role",
        is_system=False,
    )
    db.add(role)
    db.flush()

    for key in permission_keys:
        perm = _ensure_permission(db, key)
        db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.flush()
    return role


@pytest.fixture
def auth_headers(db: Session):
    """
    Create a real active user, optional tenant membership/permissions, and
    return current JWT Bearer headers used by the app.
    """
    def _make(
        *,
        user_id: int | None = None,
        tenant_id: int | None = None,
        permissions: tuple[str, ...] = (),
        is_superadmin: bool = False,
    ) -> dict[str, str]:
        user = _ensure_user(db, user_id=user_id, is_superadmin=is_superadmin)
        if tenant_id is not None and not is_superadmin:
            role = _ensure_role_with_permissions(db, tuple(permissions))
            existing = (
                db.query(UserTenant)
                .filter(UserTenant.user_id == user.id, UserTenant.tenant_id == tenant_id)
                .first()
            )
            if existing is None:
                db.add(UserTenant(user_id=user.id, tenant_id=tenant_id, role_id=role.id))
            else:
                existing.role_id = role.id
            db.flush()

        token = create_access_token(
            user.id,
            {"email": user.email, "is_superadmin": user.is_superadmin},
        )
        return {"Authorization": f"Bearer {token}"}

    return _make
