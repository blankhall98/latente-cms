# app/utils/payload_guard.py
from __future__ import annotations
import json
from fastapi import HTTPException
from app.core.settings import settings

def enforce_entry_data_size(data: dict):
    if settings.MAX_ENTRY_DATA_KB <= 0:
        return
    try:
        b = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        kb = len(b) / 1024.0
    except Exception:
        # Si no se puede serializar, considerar como inválido
        raise HTTPException(status_code=400, detail="Invalid JSON in data")
    if kb > settings.MAX_ENTRY_DATA_KB:
        raise HTTPException(
            status_code=413,
            detail=f"Payload too large: data is {kb:.1f}KB, limit is {settings.MAX_ENTRY_DATA_KB}KB",
        )
