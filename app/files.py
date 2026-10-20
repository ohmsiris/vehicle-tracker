"""
Validates and normalizes one uploaded file before it goes to the model.

Type is decided from the file's own first bytes, never from the browser's
claimed content-type or the filename -- a renamed or mislabeled upload
should fail here with a clear message, not as a confusing API error.

Images are only re-encoded when they need it (too big for the API, or
carrying an EXIF rotation the model wouldn't apply on its own); a normal
LINE-sized photo (~350 KB) goes through byte-for-byte untouched so
extraction quality isn't degraded by a pointless second JPEG pass.
"""
import io

from PIL import Image, ImageOps

# Anthropic's image limit is 5 MB per image; stay well under it. Longest
# side capped at 2400px -- plenty to read handwriting, and avoids the
# model's own internal downscale doing it less carefully.
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_LONGEST_SIDE = 2400
REENCODE_QUALITY = 88


class UnsupportedFile(ValueError):
    """Message is user-facing (Thai), safe to show on the upload page."""


def sniff_media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    raise UnsupportedFile("ไฟล์นี้ไม่ใช่รูปภาพ (JPG/PNG/WEBP) หรือ PDF")


def _needs_reencode(img: Image.Image, size_bytes: int) -> bool:
    exif_orientation = img.getexif().get(0x0112, 1)
    return (
        size_bytes > MAX_IMAGE_BYTES
        or max(img.size) > MAX_LONGEST_SIDE
        or exif_orientation not in (None, 1)
    )


def prepare_file(data: bytes) -> tuple[bytes, str]:
    """Returns (bytes_to_send, media_type). Raises UnsupportedFile."""
    if not data:
        raise UnsupportedFile("ไฟล์ว่างเปล่า")
    media_type = sniff_media_type(data)
    if media_type == "application/pdf":
        return data, media_type

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # corrupt/truncated image
        raise UnsupportedFile("เปิดรูปภาพนี้ไม่ได้ ไฟล์อาจเสียหาย") from exc

    if not _needs_reencode(img, len(data)):
        return data, media_type

    img = ImageOps.exif_transpose(img)  # bake the rotation in
    if max(img.size) > MAX_LONGEST_SIDE:
        img.thumbnail((MAX_LONGEST_SIDE, MAX_LONGEST_SIDE), Image.LANCZOS)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=REENCODE_QUALITY, optimize=True)
    return out.getvalue(), "image/jpeg"
