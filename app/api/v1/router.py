# app/api/v1/router.py
from fastapi import APIRouter

from .endpoints import health, content
from app.api.v1.endpoints import schemas as schemas_endpoints
from app.api.v1.endpoints import owa_popup as owa_popup_endpoints
from app.api.v1 import auth as auth_endpoints

from app.api.v1.endpoints import users as users_endpoints
from app.api.v1.endpoints import tenants as tenants_endpoints
from app.api.v1.endpoints import members as members_endpoints
from app.api.v1.endpoints import roles as rbac_endpoints

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(content.router, prefix="/content", tags=["content"])
api_router.include_router(auth_endpoints.router, prefix="/auth")

# Admin core endpoints used by web UI
api_router.include_router(users_endpoints.router)      # /users
api_router.include_router(tenants_endpoints.router)    # /tenants
api_router.include_router(members_endpoints.router)    # /members
api_router.include_router(rbac_endpoints.router)       # /rbac
api_router.include_router(schemas_endpoints.router)    # /schemas

# OWA pop-up public submit endpoint
api_router.include_router(owa_popup_endpoints.router)  # /owa/popup-submissions
