# app/utils/idempotency.py
from __future__ import annotations
import time
import threading
from typing import Dict, Tuple, Optional
from starlette.responses import Response
from app.core.config import settings

CacheValue = Tuple[float, int, bytes, Dict[str, str]]  # (expires_at, status_code, body, headers)

class IdempotencyCache:
    def __init__(self):
        self._store: Dict[str, CacheValue] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[CacheValue]:
        now = time.time()
        with self._lock:
            v = self._store.get(key)
            if not v:
                return None
            if v[0] < now:
                self._store.pop(key, None)
                return None
            return v

    def set_success(self, key: str, status_code: int, body: bytes, headers: Dict[str, str]):
        ttl = settings.IDEMPOTENCY_TTL_SECONDS
        exp = time.time() + ttl
        with self._lock:
            self._store[key] = (exp, status_code, body, headers)

idempotency_cache = IdempotencyCache()

def maybe_replay_idempotent(key: Optional[str]) -> Optional[Response]:
    if not settings.IDEMPOTENCY_ENABLED or not key:
        return None
    cached = idempotency_cache.get(key)
    if not cached:
        return None
    exp, code, body, headers = cached
    resp = Response(content=body, status_code=code, media_type="application/json")
    for k, v in headers.items():
        resp.headers[k] = v
    resp.headers["Idempotent-Replay"] = "true"
    return resp

def remember_idempotent_success(key: Optional[str], response: Response):
    if not settings.IDEMPOTENCY_ENABLED or not key:
        return
    # Guardar sólo éxitos 2xx
    if 200 <= response.status_code < 300:
        body = response.body if response.body is not None else b""
        # headers seguros de reenviar
        safe_headers = {k: v for k, v in response.headers.items() if k.lower() in (
            "content-type", "etag", "cache-control"
        )}
        idempotency_cache.set_success(key, response.status_code, body, safe_headers)
