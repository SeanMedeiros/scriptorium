"""Model Manager: Zenodo-backed discovery, installation, versioning and recommendation.

Lifecycle of a version installed from Zenodo:
    (preview, licence shown) → DOWNLOADING → INSTALLING → INSTALLED → VALIDATED | INCOMPATIBLE
                                   └──────────────┴────────────────→ FAILED (network, size, checksum)
A model's *active* version changes only by an explicit administrator action (pinning /
rollback). Newer Zenodo versions are reported as "update available", never installed silently.
"""
from __future__ import annotations

import re
import tempfile
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ModelLicense, ModelSource, ModelStatus, ModelTask, ModelVersion, RecognitionModel
from app.registry import local
from app.registry.zenodo import RecordInfo, ZenodoClient, ZenodoError, compatibility_precheck, parse_model_card

# Curated starting catalogue: only identifiers. Every displayed fact is fetched live from Zenodo.
# `suggested_curation` is applied on installation and is always shown as curated, with its basis.
CATALOG = [
    {
        "identifier": "10.5281/zenodo.14543904",
        "family": "TraPrInq",
        "suggested_curation": {
            "languages": ["por"], "period_start": 1500, "period_end": 1899, "script_type": "manuscript",
            "basis": "Zenodo record title: 'Transcription model to recognize Portuguese handwritten texts from the 16th to 19th centuries'",
        },
    },
    {
        "identifier": "10.5281/zenodo.10066218",  # CATMuS Medieval concept DOI → resolves to the latest version
        "family": "CATMuS",
        "suggested_curation": {
            "script_type": "manuscript", "period_start": 500, "period_end": 1500,
            "transcription_convention": "graphematic; abbreviations not resolved",
            "basis": "Zenodo record description: 'strictly graphematic transcriptions. No abbreviations are resolved'; "
                     "title 'CATMuS Medieval'",
        },
    },
]


class RegistryError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def detect_normalization(text: str) -> dict | None:
    """Find an explicit Unicode normalisation statement in source text. Returns form + quote."""
    for form, pat in (("NFD", r"(?:\bNFD\b|Normali[sz]ation Form Decomposed)"), ("NFC", r"(?:\bNFC\b|Normali[sz]ation Form Composed)"),
                      ("NFKD", r"\bNFKD\b"), ("NFKC", r"\bNFKC\b")):
        if m := re.search(pat, text, re.I):
            start = max(0, m.start() - 60)
            return {"form": form, "quote": text[start:m.end() + 40].replace("\n", " ").strip(), "source": "record description"}
    return None


def ensure_license(db: Session, license_id: str | None, source: str) -> str | None:
    if not license_id:
        return None
    if db.get(ModelLicense, license_id) is None:
        db.add(ModelLicense(id=license_id, source=source,
                            url=f"https://spdx.org/licenses/{license_id.upper()}.html" if license_id.lower().startswith("cc-") else None))
        db.flush()
    return license_id


def _slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:100] or "model"


def _model_file(record: RecordInfo, file_key: str | None):
    files = record.model_files
    if file_key:
        files = [f for f in files if f.key == file_key]
    if not files:
        raise RegistryError("no_model_file", "The record contains no Kraken model file (.mlmodel / .safetensors)"
                            + (f" named '{file_key}'" if file_key else ""), {"files": [f.key for f in record.files]})
    if len(files) > 1 and not file_key:
        raise RegistryError("ambiguous_file", "The record contains several model files; choose one",
                            {"files": [f.key for f in files]})
    return files[0]


# ------------------------------------------------------------------ preview
def preview(db: Session, identifier: str, client: ZenodoClient | None = None) -> dict:
    """Everything an administrator must see before installing: metadata, authors, licence,
    files with checksums, versions (latest marked), model card, compatibility pre-check,
    and what is already installed."""
    from app.recognition.engine import kraken_version

    z = client or ZenodoClient()
    record = z.record(identifier)
    versions = z.versions(record.record_id)
    latest_id = versions[0].record_id if versions else record.record_id
    card = parse_model_card(record, z.small_file)
    mfiles = record.model_files
    pre = compatibility_precheck(record, mfiles[0] if len(mfiles) == 1 else (mfiles[0] if mfiles else None), card, kraken_version())
    installed = {v.record_id: v for v in db.scalars(select(ModelVersion).where(
        ModelVersion.record_id.in_([v.record_id for v in versions] or [record.record_id])))}
    by_checksum: dict[str, list[str]] = {}
    vlist = []
    for v in versions:
        mf = [f for f in v.files if f.is_kraken_model]
        for f in mf:
            by_checksum.setdefault(f.checksum, []).append(v.version or v.record_id)
        iv = installed.get(v.record_id)
        vlist.append({"record_id": v.record_id, "version": v.version, "publication_date": v.publication_date,
                      "doi": v.doi, "license_id": v.license_id, "is_latest": v.record_id == latest_id,
                      "model_files": [{"key": f.key, "size": f.size, "checksum": f.checksum} for f in mf],
                      "installed": {"id": str(iv.id), "status": iv.status.value} if iv else None})
    for v in vlist:  # flag versions that publish the very same binary as another version
        v["identical_binary_as"] = sorted({lab for f in v["model_files"] for lab in by_checksum.get(f["checksum"], [])}
                                          - {v["version"] or v["record_id"]})
    norm = detect_normalization(record.description)
    return {
        "record": record.to_dict(),
        "latest_record_id": latest_id,
        "is_latest": record.record_id == latest_id,
        "versions": vlist,
        "model_files": [{"key": f.key, "size": f.size, "checksum": f.checksum} for f in mfiles],
        "model_card": card,
        "source_reported_accuracy": (card.get("card") or {}).get("accuracy"),
        "unicode_normalization": norm,
        "compatibility": pre,
        "license": {"id": record.license_id,
                    "notice": "Installing stores the model on institution-controlled infrastructure for local inference only. "
                              "SCRIPTORIUM never redistributes model files. Check that the licence permits your use."},
        "catalog_entry": next((c for c in CATALOG if c["identifier"].endswith(record.concept_id or "-")
                               or c["identifier"].endswith(record.record_id)), None),
    }


# ------------------------------------------------------------------ install
def start_install(db: Session, *, user_id: uuid.UUID, identifier: str, file_key: str | None, accept_license: bool,
                  task: ModelTask = ModelTask.RECOGNITION, family: str | None = None,
                  client: ZenodoClient | None = None) -> ModelVersion:
    z = client or ZenodoClient()
    record = z.record(identifier)
    if not accept_license:
        raise RegistryError("license_not_accepted", "The licence must be displayed and accepted before installation",
                            {"license_id": record.license_id})
    mf = _model_file(record, file_key)
    existing = db.scalar(select(ModelVersion).where(ModelVersion.record_id == record.record_id,
                                                    ModelVersion.file_name == mf.key))
    if existing and existing.status not in (ModelStatus.FAILED,):
        raise RegistryError("already_installed", f"This file is already registered (status {existing.status.value})",
                            {"model_version_id": str(existing.id)})
    card = parse_model_card(record, z.small_file)
    from app.recognition.engine import kraken_version

    pre = compatibility_precheck(record, mf, card, kraken_version())
    if pre["verdict"] == "incompatible":
        raise RegistryError("incompatible", "The record is not installable as a Kraken model", pre)
    c = card.get("card") or {}
    if (mt := c.get("model_type")) and "segmentation" in mt and "recognition" not in mt:
        task = ModelTask.SEGMENTATION

    model = db.scalar(select(RecognitionModel).where(RecognitionModel.concept_record_id == (record.concept_id or record.record_id)))
    if model is None:
        cat = next((x for x in CATALOG if x["identifier"].endswith(record.concept_id or "-")
                    or x["identifier"].endswith(record.record_id)), None)
        # Short name: the part of the title before " - ", " – ", " — " or ": " (e.g. "TraPrInq").
        base = _slugify(re.split(r"\s[-–—]\s|:\s", record.title, maxsplit=1)[0])
        slug, n = base, 2
        while db.scalar(select(RecognitionModel.id).where(RecognitionModel.slug == slug)):
            slug, n = f"{base}-{n}", n + 1
        model = RecognitionModel(
            slug=slug, name=record.title, task=task, source=ModelSource.ZENODO, concept_doi=record.concept_doi,
            concept_record_id=record.concept_id or record.record_id, description=record.description[:20000],
            family=family or (cat or {}).get("family") or ("CATMuS" if "catmus" in record.title.lower() else None),
            curated=({**cat["suggested_curation"], "curated_by": "catalogue", "curated_at": _now().isoformat()}
                     if cat else {}))
        db.add(model)
        db.flush()
    if card.get("format") == "htrmopo-v1":
        model.source_languages = list(c.get("language") or [])
    if c.get("script"):
        model.source_scripts = list(c.get("script") or [])

    norm = detect_normalization(record.description)
    version_label = record.version or f"record-{record.record_id}"
    if existing:  # retry of a FAILED installation
        mv = existing
        mv.status, mv.bytes_downloaded = ModelStatus.DOWNLOADING, 0
    else:
        mv = ModelVersion(model_id=model.id, version=version_label, file_name=mf.key, status=ModelStatus.DOWNLOADING)
        db.add(mv)
    mv.doi, mv.record_id, mv.publication_date = record.doi, record.record_id, record.publication_date
    mv.size_bytes, mv.source_checksum, mv.download_url = mf.size, mf.checksum, mf.url
    mv.license_id = ensure_license(db, record.license_id, "zenodo")
    mv.license_accepted_by_id, mv.license_accepted_at, mv.installed_by_id = user_id, _now(), user_id
    mv.unicode_normalization = norm["form"] if norm else None
    mv.model_card_format, mv.model_card, mv.compatibility = card.get("format"), card, pre
    mv.source_metadata = {"zenodo": record.to_dict(), "unicode_normalization_evidence": norm,
                          "source_reported_accuracy": c.get("accuracy")}
    local.log_event(mv, "installation requested; licence accepted", license=record.license_id, record=record.record_id,
                    file=mf.key, size=mf.size, checksum=mf.checksum)
    db.flush()
    return mv


def run_install(db: Session, version_id: uuid.UUID, client: ZenodoClient | None = None) -> ModelVersion:
    """Download, verify, install, validate. Commits progress as it goes."""
    from app.registry.zenodo import FileInfo

    mv = db.get(ModelVersion, version_id)
    if mv is None or mv.status != ModelStatus.DOWNLOADING:
        return mv
    z = client or ZenodoClient()
    f = FileInfo(key=mv.file_name, size=mv.size_bytes or 0, checksum=mv.source_checksum or "", url=mv.download_url or "")
    root = get_settings().model_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=root, prefix=".download-")) / f.key

    def progress(done: int, total: int) -> None:
        mv.bytes_downloaded = done
        db.commit()

    try:
        local.log_event(mv, "download started", url=f.url)
        db.commit()
        digests = z.download(f, tmp, on_progress=progress)
        local.log_event(mv, "download complete; size and checksum match the Zenodo record", **digests)
        mv.status = ModelStatus.INSTALLING
        db.commit()
        dest, digests2, size = local.install_file(tmp, mv.file_name)
        same = db.scalar(select(ModelVersion).where(ModelVersion.sha256 == digests2["sha256"], ModelVersion.id != mv.id,
                                                    ModelVersion.status == ModelStatus.VALIDATED).limit(1))
        mv.file_path, mv.sha256, mv.size_bytes = str(dest), digests2["sha256"], size
        mv.status, mv.installed_at = ModelStatus.INSTALLED, _now()
        local.log_event(mv, "file installed", path=str(dest), sha256=digests2["sha256"])
        if same:
            local.log_event(mv, f"identical binary to already installed {same.model.name} {same.version}",
                            other_version_id=str(same.id))
        db.commit()
        local.validate(db, mv)
        model = mv.model
        if mv.status == ModelStatus.VALIDATED and model.active_version_id is None:
            model.active_version_id = mv.id
            local.log_event(mv, "set as active version (first validated version of this model)")
        db.commit()
    except ZenodoError as e:
        db.rollback()
        mv = db.get(ModelVersion, version_id)
        mv.status = ModelStatus.FAILED
        local.log_event(mv, "installation failed", error={"code": e.code, "message": e.message, "details": e.details})
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        mv = db.get(ModelVersion, version_id)
        mv.status = ModelStatus.FAILED
        local.log_event(mv, "installation failed", error={"code": "internal_error", "message": f"{type(e).__name__}: {e}"})
        db.commit()
    finally:
        import shutil

        shutil.rmtree(tmp.parent, ignore_errors=True)
    return mv


def fail_interrupted_installs(db: Session, older_than_seconds: int = 900) -> list[str]:
    """Installations stuck in DOWNLOADING/INSTALLING (worker lost) become FAILED; they can be retried."""
    cutoff = _now() - timedelta(seconds=older_than_seconds)
    out = []
    for mv in db.scalars(select(ModelVersion).where(ModelVersion.status.in_([ModelStatus.DOWNLOADING, ModelStatus.INSTALLING]),
                                                    ModelVersion.updated_at < cutoff)):
        mv.status = ModelStatus.FAILED
        local.log_event(mv, "installation interrupted (no progress); marked failed — retry to resume")
        out.append(str(mv.id))
    db.commit()
    return out


# ------------------------------------------------------------------ versions
def set_active_version(db: Session, model: RecognitionModel, version: ModelVersion) -> None:
    if version.model_id != model.id:
        raise RegistryError("wrong_model", "The version belongs to another model")
    if version.status != ModelStatus.VALIDATED:
        raise RegistryError("not_validated", "Only validated versions can be activated")
    previous = model.active_version_id
    model.active_version_id = version.id
    local.log_event(version, "activated", previous_active_version_id=str(previous) if previous else None)


def check_updates(db: Session, model: RecognitionModel, client: ZenodoClient | None = None) -> dict:
    """Compare installed versions with the latest Zenodo version. Never installs anything."""
    if not model.concept_record_id or model.source != ModelSource.ZENODO:
        return {"checked": False, "reason": "model is not managed through Zenodo"}
    z = client or ZenodoClient()
    any_version = next((v for v in model.versions if v.record_id), None)
    latest = z.latest(any_version.record_id if any_version else model.concept_record_id)
    installed_records = {v.record_id for v in model.versions if v.status != ModelStatus.FAILED}
    installed_checksums = {v.source_checksum for v in model.versions if v.status == ModelStatus.VALIDATED}
    latest_checksums = {f.checksum for f in latest.model_files}
    new_record = latest.record_id not in installed_records
    new_binary = bool(latest_checksums - installed_checksums)
    model.latest_record_id, model.latest_version_label = latest.record_id, latest.version
    model.update_available = new_record and new_binary
    model.last_update_check_at = _now()
    note = None
    if new_record and not new_binary:
        note = f"Zenodo version {latest.version} publishes a model file identical to an installed version (metadata-only revision)."
    return {"checked": True, "latest_record_id": latest.record_id, "latest_version": latest.version,
            "update_available": model.update_available, "note": note}


# ------------------------------------------------------------------ discovery
def discover(query: str, page: int = 1, client: ZenodoClient | None = None) -> dict:
    """Search Zenodo and describe each hit independently (no assumption shared across a family)."""
    from app.recognition.engine import kraken_version

    z = client or ZenodoClient()
    total, hits = z.search(query, page=page)
    out = []
    for r in hits:
        mfiles = r.model_files
        pre = compatibility_precheck(r, mfiles[0] if mfiles else None, {"card": None}, kraken_version())
        out.append({"record_id": r.record_id, "concept_id": r.concept_id, "title": r.title, "version": r.version,
                    "publication_date": r.publication_date, "doi": r.doi, "license_id": r.license_id,
                    "resource_type": r.resource_type, "keywords": r.keywords[:8],
                    "creators": [c.name for c in r.creators][:6],
                    "files": [{"key": f.key, "size": f.size, "kraken_model": f.is_kraken_model} for f in r.files][:12],
                    "precheck": pre})
    return {"total": total, "page": page, "items": out}


# ------------------------------------------------------------------ recommendation
def recommend(db: Session, *, languages: list[str], script_type: str | None, period_start: int | None,
              period_end: int | None) -> list[dict]:
    """Rank validated recognition models by *declared* attributes. Never claims suitability:
    every match states whether it comes from the source or from administrator curation."""
    out = []
    models = db.scalars(select(RecognitionModel).where(RecognitionModel.task == ModelTask.RECOGNITION)).all()
    for m in models:
        mv = db.get(ModelVersion, m.active_version_id) if m.active_version_id else None
        if mv is None or mv.status != ModelStatus.VALIDATED:
            continue
        cur = m.curated or {}
        matches, unknown, mismatches = [], [], []
        langs_src, langs_cur = set(m.source_languages or []), set(cur.get("languages") or [])
        if languages:
            hit = [lang for lang in languages if lang in langs_src or lang in langs_cur]
            if hit:
                matches.append({"criterion": "language", "value": hit,
                                "evidence": "source model card" if set(hit) & langs_src else "curated"})
            elif langs_src or langs_cur:
                mismatches.append({"criterion": "language", "declared": sorted(langs_src | langs_cur)})
            else:
                unknown.append("language")
        if script_type:
            if cur.get("script_type"):
                (matches if cur["script_type"] in (script_type, "mixed") else mismatches).append(
                    {"criterion": "script_type", "value": cur["script_type"], "evidence": "curated"})
            else:
                unknown.append("script_type")
        if period_start is not None or period_end is not None:
            ps, pe = cur.get("period_start"), cur.get("period_end")
            if ps is not None and pe is not None:
                lo, hi = period_start if period_start is not None else period_end, period_end if period_end is not None else period_start
                (matches if lo <= pe and hi >= ps else mismatches).append(
                    {"criterion": "period", "value": [ps, pe], "evidence": "curated"})
            else:
                unknown.append("period")
        acc = (mv.source_metadata or {}).get("source_reported_accuracy")
        out.append({
            "model_id": str(m.id), "model_version_id": str(mv.id), "name": m.name, "version": mv.version,
            "score": len(matches) - 2 * len(mismatches), "matches": matches, "mismatches": mismatches, "unknown": unknown,
            "unicode_normalization": mv.unicode_normalization,
            "transcription_convention": cur.get("transcription_convention"),
            "curation_basis": cur.get("basis"),
            "source_reported_accuracy": acc,
            "limitations": [
                "A language or period match does not establish that the model suits this document; test it on sample pages "
                "and evaluate against ground truth.",
                *([f"Accuracy {acc}% is reported by the model's authors on their own evaluation data, not measured here."]
                  if acc is not None else []),
            ],
        })
    return sorted(out, key=lambda r: (-r["score"], r["name"]))


def models_with_versions(db: Session) -> list[RecognitionModel]:
    return db.scalars(select(RecognitionModel).order_by(RecognitionModel.task, RecognitionModel.name)).all()


def count_validated(db: Session) -> int:
    return db.scalar(select(func.count(ModelVersion.id)).where(ModelVersion.status == ModelStatus.VALIDATED)) or 0
