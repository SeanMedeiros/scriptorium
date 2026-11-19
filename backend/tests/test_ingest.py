"""Upload validation, original preservation and page extraction (PNG/JPEG/TIFF/PDF)."""
import hashlib
import io
import json

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


pytestmark = pytest.mark.integration
API = "/api/v1"


@pytest.fixture
def coll(make_user):
    u = make_user("archivist@archive.org")
    p = u.post(f"{API}/projects", json={"title": "Ingestion"}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "Box 1"}).json()
    return u, p, c


def _img(fmt="PNG", size=(320, 200), color=(240, 230, 210), **save) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, format=fmt, **save)
    return b.getvalue()


def _upload(u, p, c, files, metadata=None):
    data = {"metadata": json.dumps(metadata)} if metadata else {}
    return u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents", files=files, data=data)


def test_png_upload_preserves_original_as_page_image(coll, db):
    u, p, c = coll
    raw = _img("PNG")
    r = _upload(u, p, c, [("files", ("folio-12r.png", raw, "image/png"))],
                {"languages": ["por"], "archival_reference": "PT/TT/X/1", "date_text": "1622"})
    assert r.status_code == 201, r.text
    doc = u.get(f"{API}/documents/{r.json()[0]['id']}").json()
    assert doc["ingest_status"] == "ready" and doc["title"] == "folio-12r" and doc["languages"] == ["por"]
    assert len(doc["pages"]) == 1
    page = doc["pages"][0]
    # Single-frame image: the page image IS the uploaded original, byte for byte.
    assert page["image_asset_id"] == doc["source_asset_id"]
    assert page["image"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (page["image"]["width"], page["image"]["height"]) == (320, 200)
    img = u.client.get(page["image"]["url"])
    assert img.status_code == 200 and img.content == raw
    dl = u.get(f"{API}/documents/{doc['id']}/original")
    assert dl.content == raw and dl.headers["x-content-sha256"] == hashlib.sha256(raw).hexdigest()


def test_signed_asset_url_cannot_be_forged(coll):
    u, p, c = coll
    d = _upload(u, p, c, [("files", ("a.png", _img(), "image/png"))]).json()[0]
    url = u.get(f"{API}/documents/{d['id']}").json()["pages"][0]["image"]["url"]
    assert u.client.get(url.replace("sig=", "sig=0")).status_code == 403
    assert u.client.get(url.split("&sig=")[0] + "&sig=" + "a" * 40).status_code == 403


def test_multi_page_pdf_is_rasterised_with_recorded_derivation(coll, db):
    u, p, c = coll
    buf = io.BytesIO()
    frames = [Image.new("RGB", (595, 842), (255, 255, 255 - 40 * i)) for i in range(3)]
    frames[0].save(buf, format="PDF", save_all=True, append_images=frames[1:], resolution=72)
    raw = buf.getvalue()
    d = _upload(u, p, c, [("files", ("register.pdf", raw, "application/pdf"))]).json()[0]
    doc = u.get(f"{API}/documents/{d['id']}").json()
    assert doc["ingest_status"] == "ready" and [pg["sequence"] for pg in doc["pages"]] == [1, 2, 3]
    assert [pg["source_page_number"] for pg in doc["pages"]] == [1, 2, 3]
    img = doc["pages"][1]["image"]
    assert img["kind"] == "page_image" and img["mime_type"] == "image/png" and img["dpi"] == 300
    assert img["derivation"]["operation"] == "pdf_render" and img["derivation"]["page"] == 2
    assert img["parent_asset_id"] == doc["source_asset_id"]
    # pdfium rounds the raster size up; allow one pixel.
    assert abs(img["width"] - 595 / 72 * 300) <= 1 and abs(img["height"] - 842 / 72 * 300) <= 1
    assert u.get(f"{API}/documents/{d['id']}/original").content == raw


def test_multi_frame_tiff_becomes_pages(coll):
    u, p, c = coll
    buf = io.BytesIO()
    fr = [Image.new("L", (100, 150), v) for v in (10, 120, 250)]
    fr[0].save(buf, format="TIFF", save_all=True, append_images=fr[1:])
    d = _upload(u, p, c, [("files", ("roll.tif", buf.getvalue(), "image/tiff"))]).json()[0]
    pages = u.get(f"{API}/documents/{d['id']}").json()["pages"]
    assert len(pages) == 3 and all(pg["image"]["derivation"]["operation"] == "tiff_frame" for pg in pages)


def test_exif_orientation_creates_recorded_upright_derivative(coll):
    u, p, c = coll
    im = Image.new("RGB", (300, 100), (200, 180, 160))
    exif = im.getexif()
    exif[0x0112] = 6  # rotate 90° CW to display
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif.tobytes())
    d = _upload(u, p, c, [("files", ("photo.jpg", buf.getvalue(), "image/jpeg"))]).json()[0]
    pg = u.get(f"{API}/documents/{d['id']}").json()["pages"][0]
    assert pg["image"]["derivation"]["operation"] == "exif_transpose"
    assert (pg["image"]["width"], pg["image"]["height"]) == (100, 300)
    assert pg["image_asset_id"] != d["source_asset_id"]


def test_batch_upload_creates_one_document_per_file(coll):
    u, p, c = coll
    r = _upload(u, p, c, [("files", (f"f{i}.png", _img(color=(i, i, i)), "image/png")) for i in range(4)])
    assert r.status_code == 201 and len(r.json()) == 4
    assert u.get(f"{API}/projects/{p['id']}/collections/{c['id']}/documents").json()["total"] == 4


@pytest.mark.parametrize("name,content,code", [
    ("notes.txt", b"hello world, not an image", "unsupported_type"),
    ("fake.pdf", _img("PNG"), "type_mismatch"),
    ("archive.png", b"%PDF-1.4 fake", "type_mismatch"),
    ("script.exe", b"MZ\x90\x00", "unsupported_type"),
])
def test_rejects_wrong_types_by_content_not_name(coll, name, content, code):
    u, p, c = coll
    r = _upload(u, p, c, [("files", (name, content, "application/octet-stream"))])
    assert r.status_code == 415 and r.json()["error"]["code"] == code


def test_rejects_empty_and_corrupt_files(coll):
    u, p, c = coll
    assert _upload(u, p, c, [("files", ("e.png", b"", "image/png"))]).json()["error"]["code"] == "empty_file"
    r = _upload(u, p, c, [("files", ("c.jpg", b"\xff\xd8\xff" + b"\x00" * 200, "image/jpeg"))])
    assert r.status_code == 422 and r.json()["error"]["code"] == "corrupt_image"


def test_decompression_bomb_guard(coll, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_image_pixels", 10_000)
    u, p, c = coll
    r = _upload(u, p, c, [("files", ("big.png", _img(size=(200, 200)), "image/png"))])
    assert r.status_code == 422 and r.json()["error"]["code"] == "image_too_large"


def test_upload_size_limit(coll, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    u, p, c = coll
    r = _upload(u, p, c, [("files", ("x.png", _img(size=(400, 400), color=(1, 2, 3)) + b"\x00" * 2000, "image/png"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_upload_requires_permission(coll, make_user):
    u, p, c = coll
    reader = make_user("reader@archive.org")
    u.put(f"{API}/projects/{p['id']}/members", json={"email": reader.email, "role": "read_only"})
    r = _upload(reader, p, c, [("files", ("a.png", _img(), "image/png"))])
    assert r.status_code == 403
    outsider = make_user("outsider@archive.org")
    assert _upload(outsider, p, c, [("files", ("a.png", _img(), "image/png"))]).status_code == 404


def test_stored_assets_are_immutable(coll, db):
    u, p, c = coll
    _upload(u, p, c, [("files", ("a.png", _img(), "image/png"))])
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE image_assets SET width = 1"))
    db.rollback()


def test_document_metadata_validation_and_search(coll):
    u, p, c = coll
    bad = _upload(u, p, c, [("files", ("a.png", _img(), "image/png"))], {"languages": ["Portuguese"]})
    assert bad.status_code == 422
    _upload(u, p, c, [("files", ("Carta do bispo.png", _img(), "image/png"))], {"archival_reference": "PT/ADB/X/77"})
    _upload(u, p, c, [("files", ("Rol de confessados.png", _img(color=(9, 9, 9)), "image/png"))])
    base = f"{API}/projects/{p['id']}/collections/{c['id']}/documents"
    assert u.get(base, params={"q": "bispo"}).json()["total"] == 1
    assert u.get(base, params={"q": "adb/x/77"}).json()["total"] == 1


def test_dashboard_counts_documents_pages_and_storage_from_rows(coll, db):
    u, p, c = coll
    raws = [_img(color=(i * 40, 0, 0)) for i in range(3)]
    for i, raw in enumerate(raws):
        _upload(u, p, c, [("files", (f"d{i}.png", raw, "image/png"))])
    d = u.get(f"{API}/dashboard").json()
    assert (d["documents"], d["pages"], d["processed_pages"]) == (3, 3, 0)
    assert d["storage_bytes"] == sum(len(r) for r in raws)
    assert d["jobs"] == {"queued": 0, "processing": 0, "completed": 0, "failed": 0, "cancelled": 0}
    assert db.scalar(text("SELECT count(*) FROM pages")) == 3
