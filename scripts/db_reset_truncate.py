# scripts/db_reset_truncate.py
from sqlalchemy import text
from app.db.session import SessionLocal

TABLES = [
    "content_audit_logs",
    "entry_versions",
    "entries",
    "section_schemas",
    "sections",
    "webhook_endpoints",
    "user_tenants",
    "role_permissions",
    "permissions",
    "roles",
    "api_keys",
    "tenants",
    "users",
]

def run() -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET session_replication_role = 'replica';"))
        for t in TABLES:
            db.execute(text(f'TRUNCATE TABLE "{t}" RESTART IDENTITY CASCADE;'))
        db.execute(text("SET session_replication_role = 'origin';"))
        db.commit()
        print("[OK] TRUNCATE + RESTART IDENTITY de todas las tablas núcleo.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run()
