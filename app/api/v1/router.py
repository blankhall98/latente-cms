# app/api/v1/router.py
from fastapi import APIRouter
from .endpoints import health, content
from app.api.v1.endpoints import schemas as schemas_endpoints

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(content.router, prefix="/content", tags=["content"])
api_router.include_router(schemas_endpoints.router)
