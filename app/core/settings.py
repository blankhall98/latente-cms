# app/core/settings.py
from __future__ import annotations

import os, json
from typing import List, Union
from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ============== App / API ==============
    APP_NAME: str = "Latente CMS Core"
    API_V1_STR: str = "/api/v1"
    ENV: str = "dev"
    DEBUG: bool = True

    # ============== Auth / JWT =============
    JWT_SECRET_KEY: str = "dev-secret"
    JWT_ALGORITHM: str = "HS256"

    # Canónicos
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(60)
    JWT_REFRESH_TOKEN_EXPIRE_MINUTES: int = Field(60 * 24 * 7)

    # Legacy (fallback si existe)
    JWT_ACCESS_EXPIRE_MIN: int | None = None

    # Helpers de expiración normalizados
    @property
    def ACCESS_MIN(self) -> int:
        return int(self.JWT_ACCESS_EXPIRE_MIN or self.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    @property
    def REFRESH_MIN(self) -> int:
        return int(self.JWT_REFRESH_TOKEN_EXPIRE_MINUTES)

    # ========= Preview Tokens (UI preview) =========
    PREVIEW_TOKEN_SECRET: str | None = None
    PREVIEW_TOKEN_EXPIRE_SECONDS: int = 900  # 15 min

    @property
    def PREVIEW_SECRET(self) -> str:
        # usa el secreto propio o cae al JWT_SECRET_KEY
        return self.PREVIEW_TOKEN_SECRET or self.JWT_SECRET_KEY

    # ================== DB ==================
    DATABASE_URL: str

    @property
    def SQLALCHEMY_DATABASE_URL(self) -> str:
        """
        Normalize URLs for SQLAlchemy using psycopg2 driver on Heroku.
        Heroku commonly provides:
          - postgres://user:pass@host/db
        We want:
          - postgresql+psycopg2://user:pass@host/db
        Also coerce any psycopg v3 slug to psycopg2.
        """
        url = self.DATABASE_URL or ""
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg2://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg2://", 1)
        if url.startswith("postgresql+psycopg://"):
            return url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
        return url

    # ================= CORS =================
    BACKEND_CORS_ORIGINS: List[Union[str, AnyHttpUrl]] = []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        """
        Acepta:
        - JSON list válido: '["https://a","http://b"]'
        - Lista malformada con corchetes sin comillas: [https://a,http://b]
        - CSV sin corchetes: 'https://a,http://b'
        - Vacío / None -> []
        """
        if v in (None, "", [], ()):
            return []
        if isinstance(v, (list, tuple)):
            return list(v)

        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []

            # Caso JSON bien formado
            if s.startswith("[") and s.endswith("]"):
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    # Fallback: quitar corchetes y tratar como CSV
                    inner = s[1:-1].strip()
                    if not inner:
                        return []
                    return [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]

            # Caso CSV plano
            return [item.strip() for item in s.split(",") if item.strip()]

        # Cualquier otro tipo: lo entregamos y dejamos que Pydantic lo procese
        return v

    @property
    def CORS_ORIGINS(self) -> List[str]:
        # Siempre devuelve List[str] (convirtiendo AnyHttpUrl a str)
        return [str(x) for x in (self.BACKEND_CORS_ORIGINS or [])]

    # ====== Rate limit / tamaño / idempot ======
    RATELIMIT_ENABLED: bool = os.getenv("RATELIMIT_ENABLED", "false").lower() == "true"
    RATELIMIT_WRITE_PER_MIN: int = int(os.getenv("RATELIMIT_WRITE_PER_MIN", "60"))
    RATELIMIT_DELIVERY_PER_MIN: int = int(os.getenv("RATELIMIT_DELIVERY_PER_MIN", "200"))
    RATELIMIT_PREVIEWTOKEN_PER_MIN: int = int(os.getenv("RATELIMIT_PREVIEWTOKEN_PER_MIN", "20"))

    MAX_ENTRY_DATA_KB: int = int(os.getenv("MAX_ENTRY_DATA_KB", "256"))

    IDEMPOTENCY_ENABLED: bool = os.getenv("IDEMPOTENCY_ENABLED", "true").lower() == "true"
    IDEMPOTENCY_TTL_SECONDS: int = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400"))

    # ====== Uploads (Firebase Storage) ======
    FIREBASE_CREDENTIALS_PATH: str | None = os.getenv("FIREBASE_CREDENTIALS_PATH")
    FIREBASE_STORAGE_BUCKET: str | None = os.getenv("FIREBASE_STORAGE_BUCKET")
    UPLOAD_MAX_MB: int = int(os.getenv("UPLOAD_MAX_MB", "15"))
    UPLOAD_TENANT_SLUGS: str = os.getenv("UPLOAD_TENANT_SLUGS", "anro")

    # ============== Webhooks (Paso 20) ==============
    WEBHOOKS_ENABLED: bool = os.getenv("WEBHOOKS_ENABLED", "false").lower() == "true"
    WEBHOOKS_TIMEOUT_SECONDS: float = float(os.getenv("WEBHOOKS_TIMEOUT_SECONDS", "3"))
    WEBHOOKS_MAX_RETRIES: int = int(os.getenv("WEBHOOKS_MAX_RETRIES", "3"))
    WEBHOOKS_BACKOFF_SECONDS: float = float(os.getenv("WEBHOOKS_BACKOFF_SECONDS", "0.5"))
    WEBHOOKS_SYNC_FOR_TEST: bool = os.getenv("WEBHOOKS_SYNC_FOR_TEST", "false").lower() == "true"
    WEBHOOKS_DEFAULT_EVENTS: List[str] = Field(
        default_factory=lambda: ["content.published", "content.unpublished", "content.archived"]
    )
    WEBHOOKS_SIGNING_ALG: str = "HMAC-SHA256"

    @field_validator("WEBHOOKS_DEFAULT_EVENTS", mode="before")
    @classmethod
    def _parse_webhook_events(cls, v):
        """
        Accept JSON list (preferred) or bracket/comma string like:
        ["a","b"]  or  [a,b]  or  a,b
        """
        if v in (None, "", []):
            return ["content.published", "content.unpublished", "content.archived"]
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("[") and s.endswith("]"):
                # try strict JSON first
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except Exception:
                    # fallback: strip brackets and split
                    s = s[1:-1]
            # comma-separated fallback
            return [item.strip().strip('"').strip("'") for item in s.split(",") if item.strip()]
        return v

    # ======== Cookies de sesión (UI admin) ========
    SESSION_COOKIE_NAME: str = "latente_session"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: str = "lax"

    # ============== Pydantic v2 ==============
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()

