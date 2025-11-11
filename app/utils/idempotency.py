from __future__ import annotations

import time
import threading
from typing import Dict, Tuple, Optional

from starlette.responses import Response

from app.core.settings import settings

# (expires_at, status_code, body, headers)
CacheValue = Tuple[float, int, bytes, Dict[str, str]]


class IdempotencyCache:
    def __init__(self) -> None:
        self._store: Dict[str, CacheValue] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[CacheValue]:
        now = time.time()
        with self._lock:
            v = self._store.get(key)
            if not v:
                return None
            # TTL expired → evict
            if v[0] < now:
                self._store.pop(key, None)
                return None
            return v

    def set_success(self, key: str, status_code: int, body: bytes, headers: Dict[str, str]) -> None:
        ttl = float(getattr(settings, "IDEMPOTENCY_TTL_SECONDS", 0) or 0)
        exp = time.time() + max(0.0, ttl)
        # store only simple string headers
        clean_headers: Dict[str, str] = {str(k): str(v) for k, v in (headers or {}).items()}
        with self._lock:
            self._store[key] = (exp, int(status_code), body, clean_headers)


idempotency_cache = IdempotencyCache()


def maybe_replay_idempotent(key: Optional[str]) -> Optional[Response]:
    """
    If an identical successful response was cached for this Idempotency-Key,
    return a Response replaying it (with a marker header).
    """
    if not getattr(settings, "IDEMPOTENCY_ENABLED", False) or not key:
        return None
    cached = idempotency_cache.get(key)
    if not cached:
        return None
    _, code, body, headers = cached
    resp = Response(content=body, status_code=code, media_type=headers.get("content-type", "application/json"))
    for k, v in headers.items():
        resp.headers[k] = v
    resp.headers["Idempotent-Replay"] = "true"
    return resp


def remember_idempotent_success(key: Optional[str], response: Response) -> None:
    """
    Cache successful (2xx) responses for a given Idempotency-Key so we can replay them.
    """
    if not getattr(settings, "IDEMPOTENCY_ENABLED", False) or not key:
        return
    if not (200 <= int(response.status_code) < 300):
        return

    # Body bytes (JSONResponse already has .body set)
    body = getattr(response, "body", None)
    if body is None:
        body = b""

    # Safe headers to forward on replay
    safe_names = {"content-type", "etag", "cache-control", "last-modified"}
    safe_headers = {k: v for k, v in response.headers.items() if k.lower() in safe_names}

    idempotency_cache.set_success(key, int(response.status_code), body, safe_headers)

