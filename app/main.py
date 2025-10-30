# app/main.py
from app.core.config import create_app
from app.core.logging import configure_logging
from app.api.v1.router import api_router
from app.core.settings import settings

from app.api.delivery.router import router as delivery_router

app = create_app()
configure_logging()

app.include_router(api_router, prefix=settings.API_V1_STR)

# 👉 Delivery pública (fuera de /api/v1)
app.include_router(delivery_router)
