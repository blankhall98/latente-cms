# app/core/config.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .settings import settings

def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME)

    if settings.BACKEND_CORS_ORIGINS:
        origins = settings.CORS_ORIGINS
        cors_kwargs = {
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }

        if "*" in origins:
            # Use regex-based reflection instead of literal "*" so browsers
            # can accept cross-origin responses when credentials are included.
            cors_kwargs.update(
                allow_origins=[],
                allow_origin_regex=".*",
                allow_credentials=True,
            )
        else:
            cors_kwargs.update(
                allow_origins=origins,
                allow_credentials=True,
            )

        app.add_middleware(CORSMiddleware, **cors_kwargs)
    return app
