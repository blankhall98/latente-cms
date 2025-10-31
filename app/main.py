# app/main.py
from app.core.config import create_app
from app.core.logging import configure_logging
from app.api.v1.router import api_router
from app.core.settings import settings

from app.api.delivery.router import router as delivery_router
from app.web.admin.router import admin_router
from app.web.auth.router import auth_router

from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse

app = create_app()
configure_logging()

# Cookies de sesión para el mini-login
# Usa una SECRET_KEY segura en prod
app.add_middleware(SessionMiddleware, secret_key=(settings.JWT_SECRET_KEY or "dev-secret"))

if settings.RATELIMIT_ENABLED:
    from app.middleware.ratelimit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)

@app.get("/", include_in_schema=False)
def root_to_login():
    return RedirectResponse(url="/login", status_code=302)

# API
app.include_router(api_router, prefix=settings.API_V1_STR)
# Delivery pública
app.include_router(delivery_router)
# Admin (Jinja) + Auth
app.include_router(auth_router)          # /login, /logout
app.include_router(admin_router)         # /admin/*

# Static (CSS, imágenes)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


