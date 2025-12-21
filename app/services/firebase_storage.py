from __future__ import annotations

import os
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


def is_firebase_configured() -> bool:
    cred_path = settings.FIREBASE_CREDENTIALS_PATH or ""
    bucket = settings.FIREBASE_STORAGE_BUCKET or ""
    if not cred_path or not bucket:
        return False
    return os.path.exists(cred_path)


def _get_firebase_app():
    global _FIREBASE_APP
    if _FIREBASE_APP is not None:
        return _FIREBASE_APP
    try:
        _FIREBASE_APP = firebase_admin.get_app()
        return _FIREBASE_APP
    except ValueError:
        pass

    cred_path = settings.FIREBASE_CREDENTIALS_PATH or ""
    bucket = settings.FIREBASE_STORAGE_BUCKET or ""
    if not cred_path:
        raise RuntimeError("FIREBASE_CREDENTIALS_PATH is not set")
    if not os.path.exists(cred_path):
        raise RuntimeError("FIREBASE_CREDENTIALS_PATH does not exist")
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
