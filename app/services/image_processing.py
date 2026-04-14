from __future__ import annotations

import io
from typing import BinaryIO

from PIL import Image

# Formats we skip — SVG is a vector (not a raster), GIF may be animated.
_SKIP_CONTENT_TYPES = {"image/svg+xml", "image/gif"}
_SKIP_EXTENSIONS = {".svg", ".gif"}


def should_process_image(content_type: str, filename: str = "") -> bool:
    """Return True if the image should be converted to WebP."""
    ct = (content_type or "").lower()
    if not ct.startswith("image/"):
        return False
    if ct in _SKIP_CONTENT_TYPES:
        return False
    if ct == "image/webp":
        return False  # already WebP
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if f".{ext}" in _SKIP_EXTENSIONS:
        return False
    return True


def process_image_to_webp(
    file_obj: BinaryIO,
    max_width: int = 1920,
    quality: int = 82,
) -> tuple[io.BytesIO, str]:
    """
    Read an image from *file_obj*, resize it if wider than *max_width*,
    convert it to WebP, and return (BytesIO buffer, "image/webp").

    The caller is responsible for seeking file_obj to position 0 first.
    """
    img = Image.open(file_obj)

    # Normalise colour mode for WebP encoding.
    if img.mode == "P":
        # Palette mode — convert to RGBA to preserve any transparency.
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGB")

    # Resize only if the image is wider than the allowed maximum.
    if img.width > max_width:
        new_height = round(img.height * max_width / img.width)
        img = img.resize((max_width, new_height), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=4)
    buf.seek(0)
    return buf, "image/webp"
