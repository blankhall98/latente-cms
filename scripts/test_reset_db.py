# scripts/test_reset_db.py
"""
Resetea la BD de pruebas del Latente CMS:
- TRUNCATE de tablas en orden seguro
- RESTART IDENTITY
- CASCADE para respetar FKs

Uso:
  python -m scripts.test_reset_db
"""
from sqlalchemy import create_engine, text
from app.core.settings import settings

TRUNCATE_SQL = """
-- Auth / Core
TRUNCATE TABLE role_permissions RESTART IDENTITY CASCADE;
TRUNCATE TABLE user_tenants     RESTART IDENTITY CASCADE;
TRUNCATE TABLE permissions      RESTART IDENTITY CASCADE;
TRUNCATE TABLE roles            RESTART IDENTITY CASCADE;
TRUNCATE TABLE users            RESTART IDENTITY CASCADE;
TRUNCATE TABLE tenants          RESTART IDENTITY CASCADE;

-- Content
TRUNCATE TABLE entries          RESTART IDENTITY CASCADE;
TRUNCATE TABLE section_schemas  RESTART IDENTITY CASCADE;
TRUNCATE TABLE sections         RESTART IDENTITY CASCADE;

-- API Keys (si las usas en tests)
TRUNCATE TABLE api_keys         RESTART IDENTITY CASCADE;
"""

def main() -> None:
    engine = create_engine(settings.DATABASE_URL, future=True)
    print(f"[test_reset_db] Conectando a: {settings.DATABASE_URL}")
    with engine.begin() as conn:
        print("[test_reset_db] TRUNCATE + RESTART IDENTITY + CASCADE…")
        # Ejecuta bloque completo; psycopg3 soporta múltiples sentencias
        for stmt in [s for s in TRUNCATE_SQL.split(";") if s.strip()]:
            conn.execute(text(stmt))
        print("[test_reset_db] OK. BD limpia.")

if __name__ == "__main__":
    main()
