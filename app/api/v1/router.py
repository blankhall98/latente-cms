# app/api/v1/router.py
from fastapi import APIRouter

from .endpoints import health, content
from app.api.v1.endpoints import schemas as schemas_endpoints
from app.api.v1 import auth as auth_endpoints

from app.api.v1.endpoints import users as users_endpoints
from app.api.v1.endpoints import tenants as tenants_endpoints
from app.api.v1.endpoints import members as members_endpoints
from app.api.v1.endpoints import roles as rbac_endpoints

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(content.router, prefix="/content", tags=["content"])
api_router.include_router(auth_endpoints.router, prefix="/auth")

# NUEVO: Admin core para la UI
api_router.include_router(users_endpoints.router)     # /users
api_router.include_router(tenants_endpoints.router)   # /tenants
api_router.include_router(members_endpoints.router)   # /members
api_router.include_router(rbac_endpoints.router)      # /rbac
api_router.include_router(schemas_endpoints.router)   # /schemas (ya lo tenías)


