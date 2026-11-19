"""Document ingestion: file-type validation, original preservation, page extraction.

* The uploaded file is stored byte-for-byte as an ORIGINAL asset.
* Single-frame PNG/JPEG/TIFF: the page image IS the original (no re-encoding),
  unless EXIF orientation requires a rotation, which is recorded as a derived asset.
* Multi-frame TIFF and PDF: every frame/page is rasterised to lossless PNG and
  stored as a PAGE_IMAGE asset whose derivation records how it was produced.
"""
from __future__ import annotations

import importlib.metadata as md
import io
import logging
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.models import AssetKind, ImageAsset
from app.storage import StorageBackend

log = logging.getLogger(__name__)

SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF-", "application/pdf"),
]
ALLOWED_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff",
                      ".tiff": "image/tiff", ".pdf": "application/pdf"}


class IngestError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


@dataclass
class PageImage:
    asset: ImageAsset
    source_page_number: int | None


def sniff(head: bytes) -> str | None:
    for sig, mime in SIGNATURES:
        if head.startswith(sig):
            return mime
    return None


def check_declared_type(filename: str, sniffed: str) -> None:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestError("unsupported_extension", f"File extension '{ext or '(none)'}' is not accepted",
                          {"accepted": sorted(ALLOWED_EXTENSIONS)})
    if ALLOWED_EXTENSIONS[ext] != sniffed:
        raise IngestError("type_mismatch", f"The file content is {sniffed} but the name says {ALLOWED_EXTENSIONS[ext]}")


def _guard_pixels(w: int, h: int) -> None:
    limit = get_settings().max_image_pixels
    if w * h > limit:
        raise IngestError("image_too_large", f"Image of {w}×{h} px exceeds the limit of {limit} pixels",
                          {"width": w, "height": h, "limit": limit})


def _dpi(im: Image.Image) -> int | None:
    d = im.info.get("dpi")
    try:
        return int(round(float(d[0]))) if d and float(d[0]) > 1 else None
    except (TypeError, ValueError):
        return None


def _store_png(storage: StorageBackend, im: Image.Image, *, kind: AssetKind, parent: ImageAsset, derivation: dict,
               user_id: uuid.UUID | None, dpi: int | None) -> ImageAsset:
    buf = io.BytesIO()
    if dpi:
        im.save(buf, format="PNG", dpi=(dpi, dpi))
    else:
        im.save(buf, format="PNG")
    buf.seek(0)
    obj = storage.put_stream("pages", buf)
    return ImageAsset(id=uuid.uuid4(), kind=kind, storage_key=obj.key, sha256=obj.sha256, size_bytes=obj.size, mime_type="image/png",
                      width=im.width, height=im.height, dpi=dpi, parent_asset_id=parent.id, derivation=derivation,
                      created_by_id=user_id)


def store_original(storage: StorageBackend, stream: BinaryIO, filename: str, user_id: uuid.UUID | None) -> ImageAsset:
    head = stream.read(16)
    stream.seek(0)
    mime = sniff(head)
    if mime is None:
        raise IngestError("unsupported_type", "Only PNG, JPEG, TIFF and PDF files are accepted")
    check_declared_type(filename, mime)
    width = height = dpi = None
    if mime != "application/pdf":
        # Header-only read (no pixel decoding) so that the immutable asset row is complete.
        Image.MAX_IMAGE_PIXELS = get_settings().max_image_pixels
        try:
            with Image.open(stream) as im:
                width, height, dpi = im.width, im.height, _dpi(im)
        except Image.DecompressionBombError as e:
            raise IngestError("image_too_large", f"Image exceeds the pixel limit: {e}") from e
        except (UnidentifiedImageError, OSError, SyntaxError) as e:
            raise IngestError("corrupt_image", f"The image header cannot be read: {e}") from e
        _guard_pixels(width, height)
        stream.seek(0)
    obj = storage.put_stream("originals", stream)
    return ImageAsset(id=uuid.uuid4(), kind=AssetKind.ORIGINAL, storage_key=obj.key, sha256=obj.sha256,
                      size_bytes=obj.size, mime_type=mime, original_filename=filename[:500], width=width, height=height,
                      dpi=dpi, created_by_id=user_id)


def extract_pages(storage: StorageBackend, original: ImageAsset, user_id: uuid.UUID | None) -> list[PageImage]:
    """Return one PageImage per page/frame, in order. New assets are not yet added to a session."""
    s = get_settings()
    Image.MAX_IMAGE_PIXELS = s.max_image_pixels  # Pillow's own decompression-bomb guard
    if original.mime_type == "application/pdf":
        return _extract_pdf(storage, original, user_id, s.pdf_render_dpi, s.max_pages_per_document)
    with storage.open(original.storage_key) as f:
        data = f.read()
    try:
        im = Image.open(io.BytesIO(data))
        n_frames = getattr(im, "n_frames", 1)
        _guard_pixels(*im.size)
        im.load()
    except Image.DecompressionBombError as e:
        raise IngestError("image_too_large", f"Image exceeds the pixel limit: {e}") from e
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise IngestError("corrupt_image", f"The image cannot be decoded: {e}") from e
    if n_frames > s.max_pages_per_document:
        raise IngestError("too_many_pages", f"{n_frames} frames exceed the limit of {s.max_pages_per_document}")

    if n_frames == 1:
        orientation = im.getexif().get(0x0112, 1)
        if orientation in (None, 1):
            return [PageImage(original, None)]
        upright = ImageOps.exif_transpose(im)
        der = {"operation": "exif_transpose", "exif_orientation": orientation, "libraries": {"pillow": md.version("pillow")}}
        return [PageImage(_store_png(storage, upright, kind=AssetKind.PAGE_IMAGE, parent=original, derivation=der,
                                     user_id=user_id, dpi=_dpi(im)), None)]

    pages = []
    for i in range(n_frames):
        im.seek(i)
        frame = im.copy()
        _guard_pixels(*frame.size)
        der = {"operation": "tiff_frame", "frame": i, "libraries": {"pillow": md.version("pillow")}}
        pages.append(PageImage(_store_png(storage, frame, kind=AssetKind.PAGE_IMAGE, parent=original, derivation=der,
                                          user_id=user_id, dpi=_dpi(frame)), i + 1))
    return pages


def _extract_pdf(storage, original: ImageAsset, user_id, dpi: int, max_pages: int) -> list[PageImage]:
    import pypdfium2 as pdfium

    with storage.open(original.storage_key) as f:
        data = f.read()
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        raise IngestError("corrupt_pdf", f"The PDF cannot be opened: {e}") from e
    try:
        n = len(pdf)
        if n == 0:
            raise IngestError("empty_pdf", "The PDF has no pages")
        if n > max_pages:
            raise IngestError("too_many_pages", f"{n} pages exceed the limit of {max_pages}")
        version = md.version("pypdfium2")
        pages = []
        for i in range(n):
            page = pdf[i]
            w_pt, h_pt = page.get_size()
            _guard_pixels(int(w_pt / 72 * dpi), int(h_pt / 72 * dpi))
            im = page.render(scale=dpi / 72, rotation=0).to_pil()
            der = {"operation": "pdf_render", "page": i + 1, "dpi": dpi, "renderer": f"pypdfium2 {version}",
                   "page_size_pt": [round(w_pt, 2), round(h_pt, 2)]}
            pages.append(PageImage(_store_png(storage, im.convert("RGB"), kind=AssetKind.PAGE_IMAGE, parent=original,
                                              derivation=der, user_id=user_id, dpi=dpi), i + 1))
            page.close()
        return pages
    finally:
        pdf.close()


def load_image(storage: StorageBackend, asset: ImageAsset) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = get_settings().max_image_pixels
    with storage.open(asset.storage_key) as f:
        im = Image.open(io.BytesIO(f.read()))
        im.load()
    return im
