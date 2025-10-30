# scripts/reset_db.py
from __future__ import annotations
from sqlalchemy import text
from app.db.session import engine

# ⚠️ Esto borra TODO el schema public.
# Úsalo solo en tu entorno local de desarrollo.
with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE;"))
    conn.execute(text("CREATE SCHEMA public;"))
    # Extensiones útiles si las usas
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
print("[OK] public schema dropped & recreated")
