# app/core/settings.py
from __future__ import annotations

from typing import List, Union

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ================== App / API ==================
    APP_NAME: str = "Latente CMS Core"
    API_V1_STR: str = "/api/v1"
    ENV: str = "dev"

    # ================== Auth / JWT =================
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MIN: int = 60

    # ================ Preview Tokens ===============
    # Si es None, usamos JWT_SECRET_KEY como secreto por defecto
    PREVIEW_TOKEN_SECRET: str | None = None
    PREVIEW_TOKEN_EXPIRE_SECONDS: int = 900  # 15 min por defecto

    # ===================== DB ======================
    DATABASE_URL: str

    # ==================== CORS =====================
    # Acepta lista JSON en .env (recomendado) o CSV simple
    BACKEND_CORS_ORIGINS: List[Union[str, AnyHttpUrl]] = []

    # ================== Pydantic v2 ================
    # ¡No usar `class Config` en v2!
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=True,     # <- lo que antes estaba en `class Config`
        extra="ignore",
    )

    # Permite pasar BACKEND_CORS_ORIGINS como JSON o CSV
    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return list(v)
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            # Si parece JSON, intentar parsear
            if s.startswith("["):
                try:
                    import json
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass
            # Fallback: CSV
            return [item.strip() for item in s.split(",") if item.strip()]
        return v


settings = Settings()
