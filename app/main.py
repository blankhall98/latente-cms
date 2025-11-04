# app/main.py
from app.core.config import create_app
from app.core.logging import configure_logging
from app.api.v1.router import api_router
from app.core.settings import settings

from app.api.delivery.router import router as delivery_router
from app.api.delivery.preview import router as delivery_preview_router

from app.web.auth.router import router as auth_web_router
from app.web.admin.router import router as admin_router

from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute  # 👈 para ajustar openapi_extra

from fastapi import Request

app = create_app()
configure_logging()


def _inject_bearer_security(app):
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title="Latente CMS Core",
            version="1.0.0",
            description="API del CMS",
            routes=app.routes,
        )
        # Seguridad global por defecto
        openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        openapi_schema["security"] = [{"bearerAuth": []}]
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi


def _mark_delivery_routes_public(app):
    """
    Marca las rutas /delivery/... como públicas en la documentación (Swagger),
    removiendo el requisito global de bearer SOLO a nivel de OpenAPI.
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            path = route.path or ""
            if path.startswith("/delivery/"):
                # Mezcla respetando otros metadatos que ya tuviera el endpoint
                extra = dict(route.openapi_extra or {})
                extra["security"] = []  # ← anula el bearer global en docs
                route.openapi_extra = extra


_inject_bearer_security(app)

app.add_middleware(SessionMiddleware, secret_key=(settings.JWT_SECRET_KEY or "dev-secret"))

if settings.RATELIMIT_ENABLED:
    from app.middleware.ratelimit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)


@app.get("/", include_in_schema=False)
def root_smart(request: Request):
    user = (request.session or {}).get("user")
    return RedirectResponse(url="/admin" if user else "/login", status_code=302)


# API privada (JWT)
app.include_router(api_router, prefix=settings.API_V1_STR)

# Delivery pública (documentación sin bearer)
app.include_router(delivery_router)
app.include_router(delivery_preview_router)

# Web (login/admin)
app.include_router(auth_web_router)
app.include_router(admin_router)

# Static (CSS, imágenes)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# **Aplicar ajuste de OpenAPI tras montar los routers**
_mark_delivery_routes_public(app)





