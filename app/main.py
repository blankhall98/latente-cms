from __future__ import annotations

from fastapi import Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.delivery.preview import router as delivery_preview_router
from app.api.delivery.router import router as delivery_router
from app.api.v1.router import api_router
from app.core.config import create_app
from app.core.logging import configure_logging
from app.core.settings import settings

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


app = create_app()
configure_logging()

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

def _inject_bearer_security(app):
    """
    Inyecta bearerAuth globalmente en OpenAPI. Luego “blanqueamos” /delivery/*
    para que queden públicos en la documentación (solo docs; la seguridad real es la de los endpoints).
    """
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title="Latente CMS Core",
            version="1.0.0",
            description="API del CMS",
            routes=app.routes,
        )

        # Seguridad global por defecto (JWT Bearer)
        components = openapi_schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["bearerAuth"] = {
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
    Marca rutas /delivery/... como públicas en la documentación (Swagger),
    removiendo el requisito global de bearer SOLO a nivel de OpenAPI.
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            path = route.path or ""
            if path.startswith("/delivery/"):
                extra = dict(route.openapi_extra or {})
                extra["security"] = []  # ← anula el bearer global en docs para esas rutas
                route.openapi_extra = extra


# OpenAPI con bearer por defecto
_inject_bearer_security(app)

# Sesiones (para login web). Usamos JWT_SECRET_KEY como key por simplicidad local.
app.add_middleware(SessionMiddleware, secret_key=(settings.JWT_SECRET_KEY or "dev-secret"))

# Rate limit opcional (solo si está habilitado en settings)
if getattr(settings, "RATELIMIT_ENABLED", False):
    try:
        from app.middleware.ratelimit import RateLimitMiddleware
        app.add_middleware(RateLimitMiddleware)
    except Exception:
        # Si el middleware no existe o falla la importación, no bloqueamos el arranque.
        pass


@app.get("/", include_in_schema=False)
def root_smart(request: Request):
    user = (request.session or {}).get("user")
    # Si hay sesión web, mandar al admin; si no, al login.
    return RedirectResponse(url="/admin" if user else "/login", status_code=302)


# API privada (JWT)
app.include_router(api_router, prefix=settings.API_V1_STR)

# Delivery pública + Preview pública (con tokens)
app.include_router(delivery_router)
app.include_router(delivery_preview_router)

# Web (login/admin)
from app.web.auth.router import router as auth_web_router  # import tardío para evitar ciclos
from app.web.admin.router import router as admin_router
app.include_router(auth_web_router)
app.include_router(admin_router)

# Static (CSS, imágenes)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Ajuste de OpenAPI tras montar routers (para “blanquear” /delivery/* en docs)
_mark_delivery_routes_public(app)






