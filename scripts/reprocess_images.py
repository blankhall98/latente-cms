"""
scripts/reprocess_images.py
----------------------------
Retroactively convert all Firebase Storage images that are already saved in
entry JSONB data to WebP.

For every entry in the database the script:
  1. Recursively scans the entry's JSONB data for firebasestorage.googleapis.com URLs.
  2. Skips URLs that already point to a .webp file.
  3. Downloads the original blob via the Firebase Admin SDK.
  4. Converts it to WebP (resize + compression) using the same service the
     upload handler uses.
  5. Uploads the processed file to Firebase at a new path (same path, .webp ext).
  6. Replaces the old URL with the new one in the entry data and saves to Postgres.

Usage
-----
    # Preview what would change (no writes):
    python -m scripts.reprocess_images --dry-run

    # Process a single tenant:
    python -m scripts.reprocess_images --tenant anro

    # Limit to the first 20 entries (useful for a smoke-test):
    python -m scripts.reprocess_images --tenant owa --limit 20

    # Full run:
    python -m scripts.reprocess_images
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.parse
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Bootstrap: make sure the project root is on sys.path when running as
#   python -m scripts.reprocess_images   or   python scripts/reprocess_images.py
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core.settings import settings  # noqa: E402  (after sys.path patch)
from app.db.session import SessionLocal  # noqa: E402
from app.models.auth import Tenant  # noqa: E402
from app.models.content import Entry  # noqa: E402
from app.services.image_processing import process_image_to_webp  # noqa: E402
from app.services.firebase_storage import (  # noqa: E402
    _get_firebase_app,
    _normalize_bucket,
    upload_file_to_firebase,
)

try:
    from firebase_admin import storage as fb_storage
except ImportError:
    fb_storage = None  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIREBASE_HOST = "firebasestorage.googleapis.com"


def _parse_firebase_url(url: str) -> tuple[str, str] | None:
    """
    Parse a Firebase Storage download URL and return (bucket_name, blob_path).

    URL format:
      https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{encoded_path}?alt=media&token=...
    """
    try:
        parsed = urlparse(url)
        if _FIREBASE_HOST not in parsed.netloc:
            return None
        # path looks like:  /v0/b/my-bucket.appspot.com/o/uploads%2Fanro%2F...
        parts = parsed.path.split("/o/", 1)
        if len(parts) != 2:
            return None
        bucket = parts[0].replace("/v0/b/", "", 1)
        blob_path = urllib.parse.unquote(parts[1])
        return bucket, blob_path
    except Exception:
        return None


def _is_already_webp(blob_path: str) -> bool:
    root, ext = os.path.splitext(blob_path)
    return ext.lower() == ".webp"


def _webp_path(blob_path: str) -> str:
    """Replace the extension of *blob_path* with .webp."""
    root, _ = os.path.splitext(blob_path)
    return root + ".webp"


def _collect_firebase_urls(data: Any, path: str = "") -> list[tuple[str, Any]]:
    """
    Recursively walk *data* (dict / list / scalar) and return a list of
    (json_path, url_string) for every string value that looks like a
    Firebase Storage download URL.
    """
    results: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            results.extend(_collect_firebase_urls(value, f"{path}.{key}" if path else key))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            results.extend(_collect_firebase_urls(item, f"{path}[{idx}]"))
    elif isinstance(data, str) and _FIREBASE_HOST in data:
        results.append((path, data))
    return results


def _replace_url_in_data(data: Any, old_url: str, new_url: str) -> Any:
    """Return a deep copy of *data* with every occurrence of *old_url* replaced."""
    if isinstance(data, dict):
        return {k: _replace_url_in_data(v, old_url, new_url) for k, v in data.items()}
    if isinstance(data, list):
        return [_replace_url_in_data(item, old_url, new_url) for item in data]
    if isinstance(data, str) and data == old_url:
        return new_url
    return data


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _process_entry(
    entry: Entry,
    *,
    dry_run: bool,
    max_width: int,
    quality: int,
) -> tuple[dict, Any]:
    """
    Process one entry. Returns (result_dict, updated_data).
    result_dict keys: entry_id, slug, total_urls, skipped, converted, errors
    """
    result = {
        "entry_id": entry.id,
        "slug": entry.slug or "(no slug)",
        "total_urls": 0,
        "skipped": 0,
        "converted": 0,
        "errors": 0,
    }

    urls = _collect_firebase_urls(entry.data or {})
    result["total_urls"] = len(urls)

    if not urls:
        return result, entry.data

    app = _get_firebase_app()
    bucket = fb_storage.bucket(app=app)

    new_data = entry.data  # rebuilt per successful conversion

    for json_path, url in urls:
        parsed = _parse_firebase_url(url)
        if parsed is None:
            result["skipped"] += 1
            continue

        _, blob_path = parsed

        if _is_already_webp(blob_path):
            result["skipped"] += 1
            continue

        # --- Download ---
        try:
            blob = bucket.blob(blob_path)
            raw_bytes = blob.download_as_bytes()
        except Exception as exc:
            print(f"    [ERROR] entry {entry.id} | {json_path}: download failed — {exc}")
            result["errors"] += 1
            continue

        # --- Process ---
        try:
            processed_buf, _ = process_image_to_webp(
                io.BytesIO(raw_bytes),
                max_width=max_width,
                quality=quality,
            )
        except Exception as exc:
            print(f"    [ERROR] entry {entry.id} | {json_path}: processing failed — {exc}")
            result["errors"] += 1
            continue

        new_path = _webp_path(blob_path)

        if dry_run:
            print(f"    [DRY RUN] would replace: {blob_path!r} -> {new_path!r}")
            result["converted"] += 1
            continue

        # --- Upload ---
        try:
            new_url = upload_file_to_firebase(processed_buf, "image/webp", new_path)
        except Exception as exc:
            print(f"    [ERROR] entry {entry.id} | {json_path}: upload failed — {exc}")
            result["errors"] += 1
            continue

        new_data = _replace_url_in_data(new_data, url, new_url)
        result["converted"] += 1

    return result, new_data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Reprocess existing Firebase images to WebP.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    parser.add_argument("--tenant", metavar="SLUG", help="Process only this tenant slug.")
    parser.add_argument("--limit", type=int, default=0, help="Max number of entries to process (0 = all).")
    args = parser.parse_args()

    max_width = int(getattr(settings, "IMAGE_MAX_WIDTH", 1920))
    quality = int(getattr(settings, "IMAGE_WEBP_QUALITY", 82))

    db: Session = SessionLocal()
    try:
        # Build query
        q = select(Entry).join(Tenant, Tenant.id == Entry.tenant_id)
        if args.tenant:
            q = q.where(Tenant.slug == args.tenant)
        q = q.order_by(Entry.id)
        if args.limit:
            q = q.limit(args.limit)

        entries = db.scalars(q).all()

        if not entries:
            print("No entries found.")
            return

        print(f"\nLatente CMS — Image reprocessor")
        print(f"  dry_run   : {args.dry_run}")
        print(f"  tenant    : {args.tenant or 'all'}")
        print(f"  entries   : {len(entries)}")
        print(f"  max_width : {max_width}px")
        print(f"  quality   : {quality}")
        print("-" * 60)

        totals = {"total_urls": 0, "skipped": 0, "converted": 0, "errors": 0}

        for entry in entries:
            result, new_data = _process_entry(
                entry,
                dry_run=args.dry_run,
                max_width=max_width,
                quality=quality,
            )

            for k in totals:
                totals[k] += result[k]

            if result["converted"] or result["errors"]:
                print(
                    f"  entry {result['entry_id']:>5} ({result['slug']:<30}) "
                    f"urls={result['total_urls']} "
                    f"converted={result['converted']} "
                    f"skipped={result['skipped']} "
                    f"errors={result['errors']}"
                )

            # Persist updated data
            if not args.dry_run and result["converted"] > 0:
                entry.data = new_data
                db.add(entry)

        if not args.dry_run:
            db.commit()

        print("-" * 60)
        print(
            f"Done. "
            f"total_urls={totals['total_urls']} "
            f"converted={totals['converted']} "
            f"skipped={totals['skipped']} "
            f"errors={totals['errors']}"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
