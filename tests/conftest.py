# tests/conftest.py
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.settings import settings
from app.db.session import get_db

# Engine a la misma BD definida en settings (las tablas deben existir vía Alembic)
engine = create_engine(settings.DATABASE_URL, future=True)

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

