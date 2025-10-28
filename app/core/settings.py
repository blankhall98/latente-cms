from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl
from typing import List

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    APP_NAME: str = "Latente CMS Core"
    API_V1_STR: str = "/api/v1"
    ENV: str = "dev"

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MIN: int = 60

    DATABASE_URL: str
    BACKEND_CORS_ORIGINS: List[AnyHttpUrl] = []

settings = Settings()
