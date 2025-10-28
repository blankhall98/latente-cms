# scripts/db_check.py
from sqlalchemy import text
from app.db.session import engine

with engine.connect() as conn:
    ver = conn.execute(text("select version()")).scalar_one()
    db  = conn.execute(text("select current_database()")).scalar_one()
    print("OK DB:", ver)
    print("Current DB:", db)
