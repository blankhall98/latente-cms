from __future__ import annotations

import json
from fastapi import HTTPException

from app.core.settings import settings


def enforce_entry_data_size(data: dict) -> None:
    """
    Enforces a maximum serialized JSON size (in KB) for the 'data' field.
    Raises HTTP 413 on overflow, or 400 on invalid JSON.
    """
    limit_kb = float(getattr(settings, "MAX_ENTRY_DATA_KB", 0) or 0)
    if limit_kb <= 0:
        return
    try:
        # compact JSON to measure true wire-size
        b = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        kb = len(b) / 1024.0
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in data")
    if kb > limit_kb:
        raise HTTPException(
            status_code=413,
            detail=f"Payload too large: data is {kb:.1f}KB, limit is {limit_kb:.0f}KB",
        )

