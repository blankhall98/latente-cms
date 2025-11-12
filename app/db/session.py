# app/db/session.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.settings import settings

def _normalize_sqlalchemy_url(url: str) -> str:
    """
    Normalize any Heroku-style or generic Postgres URL to the explicit
    SQLAlchemy driver we have installed (psycopg2-binary).
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    return url

ENGINE_URL = _normalize_sqlalchemy_url(settings.DATABASE_URL)

engine = create_engine(
    ENGINE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,  # keep connections fresh on Heroku
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

