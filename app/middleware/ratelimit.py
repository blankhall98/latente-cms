# app/middleware/ratelimit.py
from __future__ import annotations
import time
import threading
from typing import Dict, Tuple
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from app.core.config import settings

WindowState = Tuple[int, int]  # (window_epoch_sec, count)

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Límite por minuto con ventanas fijas (simple y suficiente para staging/prod con Redis más adelante).
    - Writes (POST/PATCH) bajo /api/v1/content/*: key por (user_id, tenant_id) y fallback IP.
    - Delivery público /delivery/v1/*: key por IP.
    - Preview-token /api/v1/content/entries/*/preview-token: key por user_id.
    """

    def __init__(self, app):
        super().__init__(app)
        self._store: Dict[str, WindowState] = {}
        self._lock = threading.Lock()

    def _hit(self, key: str, limit: int) -> bool:
        now = int(time.time())
        window = now - (now % 60)
        with self._lock:
            w, c = self._store.get(key, (window, 0))
            if w != window:
                w, c = window, 0
            c += 1
            self._store[key] = (w, c)
            return c <= limit

    async def dispatch(self, request: Request, call_next):
        if not settings.RATELIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path or ""
        method = request.method.upper()
        client_ip = request.client.host if request.client else "unknown"
        user_id = request.headers.get("X-User-Id")
        tenant_id = None

        # Intentar leer tenant_id del body si JSON
        if method in ("POST", "PATCH"):
            try:
                body = await request.json()
                tenant_id = (body or {}).get("tenant_id")
            except Exception:
                tenant_id = None

        limit = None
        key = None

        # Delivery público
        if path.startswith("/delivery/v1/"):
            limit = settings.RATELIMIT_DELIVERY_PER_MIN
            key = f"deliv:{client_ip}"

        # Preview-token
        elif path.endswith("/preview-token") and path.startswith("/api/v1/content/entries/"):
            limit = settings.RATELIMIT_PREVIEWTOKEN_PER_MIN
            key = f"ptok:user:{user_id or 'anon'}"

        # Writes de contenido
        elif path.startswith("/api/v1/content/") and method in ("POST", "PATCH"):
            limit = settings.RATELIMIT_WRITE_PER_MIN
            key = f"write:u:{user_id or 'anon'}:t:{tenant_id or 'none'}"

        if limit is not None:
            allowed = self._hit(key, limit)
            if not allowed:
                return JSONResponse(
                    {"detail": "Rate limit exceeded", "key": key, "limit_per_min": limit},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )

        return await call_next(request)
