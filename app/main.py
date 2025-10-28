from app.core.config import create_app
from app.core.logging import configure_logging
from app.api.v1.router import api_router
from app.core.settings import settings

app = create_app()
configure_logging()

app.include_router(api_router, prefix=settings.API_V1_STR)
