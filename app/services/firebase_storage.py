from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import uuid

import firebase_admin
from firebase_admin import credentials, storage

from app.core.settings import settings

_FIREBASE_APP = None


def _normalize_bucket(bucket: str) -> str:
    if bucket.startswith("gs://"):
        return bucket[5:]
    return bucket


def _resolve_credentials_path() -> str:
    """
    Return a path to a valid Firebase service-account JSON file.

    Resolution order:
    1. FIREBASE_CREDENTIALS_PATH points to an existing file  (local dev)
    2. FIREBASE_SERVICE_ACCOUNT_JSON env var contains the raw JSON  (Heroku / CI)
       → write it to a temp file and return that path
    """
    cred_path = settings.FIREBASE_CREDENTIALS_PATH or ""
    if cred_path and os.path.exists(cred_path):
        return cred_path

    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if json_str:
        try:
            json.loads(json_str)  # validate before writing
        except json.JSONDecodeError as exc:
            raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        tmp.write(json_str)
        tmp.close()
        return tmp.name

    if cred_path:
        raise RuntimeError(f"FIREBASE_CREDENTIALS_PATH does not exist: {cred_path}")
    raise RuntimeError(
        "Firebase credentials not configured. "
        "Set FIREBASE_CREDENTIALS_PATH or FIREBASE_SERVICE_ACCOUNT_JSON."
    )


def is_firebase_configured() -> bool:
    bucket = settings.FIREBASE_STORAGE_BUCKET or ""
    if not bucket:
        return False
    try:
        _resolve_credentials_path()
        return True
    except RuntimeError:
        return False


def _get_firebase_app():
    global _FIREBASE_APP
    if _FIREBASE_APP is not None:
        return _FIREBASE_APP
    try:
        _FIREBASE_APP = firebase_admin.get_app()
        return _FIREBASE_APP
    except ValueError:
        pass

    cred_path = _resolve_credentials_path()
    bucket = settings.FIREBASE_STORAGE_BUCKET or ""
    if not bucket:
        raise RuntimeError("FIREBASE_STORAGE_BUCKET is not set")

    cred = credentials.Certificate(cred_path)
    _FIREBASE_APP = firebase_admin.initialize_app(
        cred,
        {"storageBucket": _normalize_bucket(bucket)},
    )
    return _FIREBASE_APP


def upload_file_to_firebase(file_obj, content_type: str | None, dest_path: str) -> str:
    app = _get_firebase_app()
    bucket = storage.bucket(app=app)

    token = uuid.uuid4().hex
    blob = bucket.blob(dest_path)
    blob.metadata = {"firebaseStorageDownloadTokens": token}
    blob.upload_from_file(file_obj, content_type=(content_type or "application/octet-stream"))

    bucket_name = _normalize_bucket(settings.FIREBASE_STORAGE_BUCKET or "")
    encoded_path = urllib.parse.quote(dest_path, safe="")
    return f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{encoded_path}?alt=media&token={token}"
