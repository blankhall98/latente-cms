# app/core/config.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .settings import settings

def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME)

    if settings.BACKEND_CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(o) for o in settings.BACKEND_CORS_ORIGINS],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    return app
