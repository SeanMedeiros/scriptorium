"""FIXTURE-ONLY unit tests of the Zenodo client's failure handling.

These use an in-process HTTP transport (httpx.MockTransport) to produce conditions
that cannot be provoked on the real Zenodo service on demand (corrupted bytes,
truncated downloads, HTTP 429). They are NOT integration tests; the real-network
tests are in test_registry_zenodo.py.
"""
import hashlib
import json

import httpx
import pytest

from app.registry.zenodo import (
    FileInfo,
    RecordInfo,
    ZenodoClient,
    ZenodoError,
    compatibility_precheck,
    parse_identifier,
    parse_model_card,
)

pytestmark = pytest.mark.fixture_only
PAYLOAD = b"kraken-model-bytes" * 1000


@pytest.mark.parametrize("ident,rid", [
    ("10.5281/zenodo.14543904", "14543904"),
    ("https://doi.org/10.5281/zenodo.15030337", "15030337"),
    ("https://zenodo.org/records/21488839", "21488839"),
    ("https://zenodo.org/api/records/21488839", "21488839"),
    ("  10066218 ", "10066218"),
])
def test_identifier_forms(ident, rid):
    assert parse_identifier(ident) == rid


@pytest.mark.parametrize("bad", ["10.1000/xyz", "https://example.org/records/1", "zenodo", ""])
def test_invalid_identifiers(bad):
    with pytest.raises(ZenodoError) as e:
        parse_identifier(bad)
    assert e.value.code == "invalid_identifier"


def _client(handler) -> ZenodoClient:
    return ZenodoClient(base_url="https://zenodo.test/api", token="", transport=httpx.MockTransport(handler), max_retries=2)


def _file(checksum: str, size: int = len(PAYLOAD)) -> FileInfo:
    return FileInfo(key="m.mlmodel", size=size, checksum=checksum, url="https://zenodo.test/api/files/m")


def test_download_verifies_checksum_and_reports_progress(tmp_path):
    client = _client(lambda req: httpx.Response(200, content=PAYLOAD))
    seen = []
    out = client.download(_file("md5:" + hashlib.md5(PAYLOAD).hexdigest()), tmp_path / "m", on_progress=lambda d, t: seen.append(d))
    assert out["sha256"] == hashlib.sha256(PAYLOAD).hexdigest() and seen[-1] == len(PAYLOAD)


def test_download_rejects_corrupted_bytes(tmp_path):
    client = _client(lambda req: httpx.Response(200, content=PAYLOAD[:-1] + b"X"))
    with pytest.raises(ZenodoError) as e:
        client.download(_file("md5:" + hashlib.md5(PAYLOAD).hexdigest()), tmp_path / "m")
    assert e.value.code == "checksum_mismatch"


def test_download_rejects_truncated_file(tmp_path):
    client = _client(lambda req: httpx.Response(200, content=PAYLOAD[:100]))
    with pytest.raises(ZenodoError) as e:
        client.download(_file("md5:" + hashlib.md5(PAYLOAD).hexdigest()), tmp_path / "m")
    assert e.value.code == "size_mismatch"


def test_download_refuses_files_without_checksum(tmp_path):
    with pytest.raises(ZenodoError) as e:
        _client(lambda req: httpx.Response(200, content=PAYLOAD)).download(_file(""), tmp_path / "m")
    assert e.value.code == "no_checksum"


def test_rate_limit_is_retried_with_retry_after():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"id": 1, "metadata": {"title": "T"}, "files": []})

    r = _client(handler).record("1234")
    assert r.title == "T" and len(calls) == 2


def test_not_found_is_explicit():
    with pytest.raises(ZenodoError) as e:
        _client(lambda req: httpx.Response(404)).record("123456")
    assert e.value.code == "record_not_found"


def _rec(files, keywords=()):
    return RecordInfo(record_id="1", concept_id="1", doi=None, concept_doi=None, title="t", version=None,
                      publication_date=None, creators=[], license_id="cc-by-4.0", description="", keywords=list(keywords),
                      resource_type="model", files=files)


def test_card_validation_flags_malformed_v0_card():
    """A card whose `authors` field is not a list must be flagged invalid (kept, not hidden)."""
    card = {"authors": "[{'name': 'X'}]", "summary": "s", "description": "d", "accuracy": 94.3, "license": "CC-BY-4.0",
            "script": ["Latn"], "name": "m.mlmodel", "graphemes": []}
    rec = _rec([FileInfo("metadata.json", 10, "md5:x", "u"), FileInfo("m.mlmodel", 10, "md5:y", "u")])
    out = parse_model_card(rec, lambda f: json.dumps(card).encode())
    assert out["format"] == "htrmopo-v0" and out["valid"] is False and out["errors"]


def test_precheck_rejects_records_without_kraken_files():
    rec = _rec([FileInfo("group1-shard1of55.bin", 10, "md5:x", "u"), FileInfo("model.json", 10, "md5:y", "u")])
    assert compatibility_precheck(rec, None, {"card": None}, "7.1.1")["verdict"] == "incompatible"


def test_precheck_rejects_cards_for_other_software():
    rec = _rec([FileInfo("m.mlmodel", 10, "md5:x", "u")])
    pre = compatibility_precheck(rec, rec.files[0], {"card": {"software_name": "PyLaia"}}, "7.1.1")
    assert pre["verdict"] == "incompatible"
