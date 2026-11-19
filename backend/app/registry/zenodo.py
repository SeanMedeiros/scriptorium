"""Zenodo REST client for the model registry.

Only facts returned by Zenodo (record metadata, files, checksums, licence, versions)
and by the model card published *in* the record are used. Nothing is inferred
without being labelled as such.

Anonymous Zenodo API limits: page size ≤ 25, rate limited (HTTP 429 with Retry-After).
An optional token (SCRIPTORIUM_ZENODO_TOKEN) raises the limits.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path

import httpx

from app import __version__
from app.config import get_settings

log = logging.getLogger(__name__)

KRAKEN_MODEL_EXTENSIONS = (".mlmodel", ".safetensors")
CARD_FILES = ("README.md", "metadata.json")
MAX_PAGE = 25


class ZenodoError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


@dataclass
class FileInfo:
    key: str
    size: int
    checksum: str  # "md5:<hex>" as published
    url: str

    @property
    def is_kraken_model(self) -> bool:
        return self.key.lower().endswith(KRAKEN_MODEL_EXTENSIONS)


@dataclass
class Creator:
    name: str
    affiliation: str | None = None
    orcid: str | None = None


@dataclass
class RecordInfo:
    record_id: str
    concept_id: str | None
    doi: str | None
    concept_doi: str | None
    title: str
    version: str | None
    publication_date: str | None
    creators: list[Creator]
    license_id: str | None
    description: str
    keywords: list[str]
    resource_type: str | None
    files: list[FileInfo]
    related: list[dict] = field(default_factory=list)
    html_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def model_files(self) -> list[FileInfo]:
        return [f for f in self.files if f.is_kraken_model]


_ID_PATTERNS = [
    re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?10\.5281/zenodo\.(\d+)$", re.I),
    re.compile(r"^https?://(?:www\.)?zenodo\.org/(?:records|record|api/records)/(\d+)(?:[/?#].*)?$", re.I),
    re.compile(r"^(\d{4,12})$"),
]


def parse_identifier(identifier: str) -> str:
    """Accept a Zenodo DOI, DOI URL, record URL or numeric id; return the record id."""
    s = identifier.strip()
    for p in _ID_PATTERNS:
        if m := p.match(s):
            return m.group(1)
    raise ZenodoError("invalid_identifier", "Expected a Zenodo DOI (10.5281/zenodo.N), a zenodo.org record URL or a record id",
                      {"identifier": identifier})


def _plain(text: str) -> str:
    text = re.sub(r"<\s*br\s*/?>|</p>|</li>|</h\d>", "\n", text or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def _record(d: dict) -> RecordInfo:
    m = d.get("metadata", {})
    creators = [Creator(name=c.get("name", ""), affiliation=c.get("affiliation"), orcid=c.get("orcid"))
                for c in m.get("creators", [])]
    files = [FileInfo(key=f["key"], size=int(f.get("size", 0)), checksum=f.get("checksum", ""),
                      url=f.get("links", {}).get("self") or f"{get_settings().zenodo_api_url}/records/{d['id']}/files/{f['key']}/content")
             for f in d.get("files", [])]
    lic = m.get("license")
    return RecordInfo(
        record_id=str(d["id"]), concept_id=str(d.get("conceptrecid")) if d.get("conceptrecid") else None,
        doi=d.get("doi") or m.get("doi"), concept_doi=d.get("conceptdoi"), title=m.get("title", ""),
        version=m.get("version"), publication_date=m.get("publication_date"), creators=creators,
        license_id=(lic.get("id") if isinstance(lic, dict) else lic), description=_plain(m.get("description", "")),
        keywords=list(m.get("keywords") or []), resource_type=(m.get("resource_type") or {}).get("type"),
        files=files, related=list(m.get("related_identifiers") or []), html_url=d.get("links", {}).get("self_html"),
    )


class ZenodoClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 30.0,
                 max_retries: int = 4, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.base = (base_url or s.zenodo_api_url).rstrip("/")
        headers = {"User-Agent": f"SCRIPTORIUM/{__version__} (model registry)", "Accept": "application/json"}
        tok = token if token is not None else s.zenodo_token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        self.max_retries = max_retries
        self.http = httpx.Client(headers=headers, timeout=timeout, follow_redirects=True, transport=transport)

    def close(self) -> None:
        self.http.close()

    def _get(self, url: str, **params) -> httpx.Response:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.get(url, params=params or None)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise ZenodoError("zenodo_unreachable", f"Zenodo is unreachable: {e}") from e
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                wait = float(r.headers.get("Retry-After", delay))
                log.warning("zenodo retry", extra={"status": r.status_code, "wait": wait})
                time.sleep(min(wait, 60))
                delay *= 2
                continue
            if r.status_code == 404:
                raise ZenodoError("record_not_found", "No such Zenodo record", {"url": url})
            if r.status_code >= 400:
                raise ZenodoError("zenodo_error", f"Zenodo answered HTTP {r.status_code}", {"url": url, "body": r.text[:500]})
            return r
        raise ZenodoError("zenodo_error", "Zenodo request failed")  # pragma: no cover

    # ------------------------------------------------------------ metadata
    def record(self, identifier: str) -> RecordInfo:
        rid = parse_identifier(identifier)
        return _record(self._get(f"{self.base}/records/{rid}").json())

    def versions(self, record_id: str) -> list[RecordInfo]:
        out: list[RecordInfo] = []
        page = 1
        while True:
            d = self._get(f"{self.base}/records/{record_id}/versions", size=MAX_PAGE, page=page, sort="version").json()
            hits = d.get("hits", {}).get("hits", [])
            out.extend(_record(h) for h in hits)
            if len(out) >= d.get("hits", {}).get("total", 0) or not hits:
                break
            page += 1
        return sorted(out, key=lambda r: (r.publication_date or "", int(r.record_id)), reverse=True)

    def latest(self, record_id: str) -> RecordInfo:
        return _record(self._get(f"{self.base}/records/{record_id}/versions/latest").json())

    def search(self, query: str, page: int = 1, size: int = MAX_PAGE) -> tuple[int, list[RecordInfo]]:
        d = self._get(f"{self.base}/records", q=query, size=min(size, MAX_PAGE), page=page).json()
        return d.get("hits", {}).get("total", 0), [_record(h) for h in d.get("hits", {}).get("hits", [])]

    def small_file(self, f: FileInfo, limit: int = 2 * 1024 * 1024) -> bytes:
        if f.size > limit:
            raise ZenodoError("file_too_large", f"{f.key} is larger than expected for a metadata file")
        return self._get(f.url).content

    # ------------------------------------------------------------ download
    def download(self, f: FileInfo, dest: Path, *, on_progress: Callable[[int, int], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None) -> dict[str, str]:
        """Stream `f` to `dest`, verifying size and the published checksum. Returns digests."""
        algo, _, expected = f.checksum.partition(":")
        if algo not in ("md5", "sha256") or not expected:
            raise ZenodoError("no_checksum", "Zenodo did not publish a usable checksum for this file", {"checksum": f.checksum})
        h_pub, h_sha = hashlib.new(algo), hashlib.sha256()
        done, last = 0, 0.0
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.http.stream("GET", f.url) as r, dest.open("wb") as out:
                if r.status_code >= 400:
                    raise ZenodoError("download_failed", f"Download failed with HTTP {r.status_code}", {"file": f.key})
                for chunk in r.iter_bytes(1 << 20):
                    out.write(chunk)
                    h_pub.update(chunk)
                    h_sha.update(chunk)
                    done += len(chunk)
                    if done > f.size:
                        raise ZenodoError("size_mismatch", "Received more bytes than Zenodo announced", {"expected": f.size})
                    if time.monotonic() - last > 1.0:
                        last = time.monotonic()
                        if on_progress:
                            on_progress(done, f.size)
                        if should_cancel and should_cancel():
                            raise ZenodoError("cancelled", "Download cancelled")
        except httpx.TransportError as e:
            raise ZenodoError("download_interrupted", f"Download interrupted: {e}", {"received": done}) from e
        if on_progress:
            on_progress(done, f.size)
        if done != f.size:
            raise ZenodoError("size_mismatch", "File size does not match the Zenodo record",
                              {"expected": f.size, "received": done})
        if h_pub.hexdigest() != expected.lower():
            raise ZenodoError("checksum_mismatch", "File checksum does not match the Zenodo record",
                              {"algorithm": algo, "expected": expected.lower(), "actual": h_pub.hexdigest()})
        return {algo: h_pub.hexdigest(), "sha256": h_sha.hexdigest()}


# ---------------------------------------------------------------- model cards
def _schema(name: str) -> dict:
    return json.loads(resources.files("htrmopo").joinpath(name).read_text())


def parse_model_card(record: RecordInfo, fetch: Callable[[FileInfo], bytes]) -> dict:
    """Read the htrmopo model card published in the record, if any.

    Returns {"format", "valid", "errors", "card"} — `card` holds the card verbatim
    (graphemes truncated). Validation uses the JSON schemas shipped with htrmopo
    (Kraken's model-repository library). Invalid cards are kept but flagged.
    """
    import jsonschema
    import yaml

    files = {f.key: f for f in record.files}
    card, fmt = None, None
    if "README.md" in files:
        text = fetch(files["README.md"]).decode("utf-8", "replace")
        if m := re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S):
            try:
                card, fmt = yaml.safe_load(m.group(1)) or {}, "htrmopo-v1"
            except yaml.YAMLError as e:
                return {"format": "htrmopo-v1", "valid": False, "errors": [f"YAML error: {e}"], "card": None}
    if card is None:
        jkey = next((k for k in files if k == "metadata.json" or (k.startswith("metadata") and k.endswith(".json"))), None)
        if jkey:
            try:
                card, fmt = json.loads(fetch(files[jkey])), "htrmopo-v0"
            except json.JSONDecodeError as e:
                return {"format": "htrmopo-v0", "valid": False, "errors": [f"JSON error: {e}"], "card": None}
    if card is None:
        return {"format": None, "valid": False, "errors": ["no model card published in the record"], "card": None}

    schema = _schema("v1.metadata.schema.json" if fmt == "htrmopo-v1" else "v0.metadata.schema.json")
    candidate = dict(card)
    if fmt == "htrmopo-v1":
        candidate.setdefault("id", record.doi or record.record_id)  # the id is assigned by the repository
    errors = sorted({e.message[:200] for e in jsonschema.Draft202012Validator(schema).iter_errors(candidate)})
    if isinstance(card.get("graphemes"), str) and len(card["graphemes"]) > 400:
        card["graphemes"] = card["graphemes"][:400] + "…"
    return {"format": fmt, "valid": not errors, "errors": errors[:20], "card": card}


def validate_card_text(text: str, pid: str = "pending") -> dict:
    """Validate a locally generated htrmopo v1 card (YAML front matter) against htrmopo's schema."""
    import jsonschema
    import yaml

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {"format": None, "valid": False, "errors": ["no YAML front matter"], "card": None}
    card = yaml.safe_load(m.group(1)) or {}
    candidate = {**card, "id": card.get("id", pid)}
    schema = _schema("v1.metadata.schema.json")
    errors = sorted({e.message[:200] for e in jsonschema.Draft202012Validator(schema).iter_errors(candidate)})
    return {"format": "htrmopo-v1", "valid": not errors, "errors": errors[:20], "card": card}


def compatibility_precheck(record: RecordInfo, model_file: FileInfo | None, card: dict, kraken_version: str) -> dict:
    """Pre-download assessment from declared facts only. The binding verdict is the
    real load test after installation."""
    reasons: list[str] = []
    verdict = "unknown"
    c = card.get("card") or {}
    if model_file is None:
        return {"verdict": "incompatible", "reasons": ["the record contains no Kraken model file (.mlmodel or .safetensors)"]}
    major = int(kraken_version.split(".")[0])
    if model_file.key.endswith(".safetensors") and major < 6:
        return {"verdict": "incompatible", "reasons": [f"safetensors models need Kraken ≥ 6 (installed {kraken_version})"]}
    sw = (c.get("software_name") or "").lower()
    if sw and sw != "kraken":
        return {"verdict": "incompatible", "reasons": [f"model card declares software '{c.get('software_name')}'"]}
    if sw == "kraken" or "kraken_pytorch" in [k.lower() for k in record.keywords]:
        verdict = "likely"
        reasons.append("declared for Kraken (" + ("model card" if sw else "Zenodo keyword kraken_pytorch") + ")")
    if hints := c.get("software_hints"):
        reasons.append(f"model card software hints: {', '.join(map(str, hints))}; installed Kraken {kraken_version}")
    mt = c.get("model_type")
    if mt and "recognition" not in mt and "segmentation" not in mt:
        verdict = "incompatible"
        reasons.append(f"model type {mt} is neither recognition nor segmentation")
    reasons.append("final verdict requires loading the file with Kraken after download")
    return {"verdict": verdict, "reasons": reasons}
