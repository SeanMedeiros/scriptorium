"""Local model installation: bundled Kraken models and manual registration of a model file.

Installation copies the file into MODEL_DIR/<sha256>/<file_name> (read-only), verifies any
checksum published by the source, registers the version and then validates it by actually
loading it with Kraken. Status reaches VALIDATED only after a successful load.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ModelSource, ModelStatus, ModelTask, ModelVersion, RecognitionModel


class ModelInstallError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def _now():
    return datetime.now(timezone.utc)


def log_event(mv: ModelVersion, message: str, **extra) -> None:
    # JSONB list must be reassigned for SQLAlchemy to detect the change.
    mv.install_log = [*(mv.install_log or []), {"ts": _now().isoformat(), "message": message, **extra}]


def file_digests(path: Path) -> dict[str, str]:
    h256, hmd5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)  # integrity comparison with Zenodo's published md5; SHA-256 is authoritative
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h256.update(chunk)
            hmd5.update(chunk)
    return {"sha256": h256.hexdigest(), "md5": hmd5.hexdigest()}


def verify_source_checksum(digests: dict[str, str], source_checksum: str | None) -> None:
    if not source_checksum:
        return
    algo, _, value = source_checksum.partition(":")
    algo = algo.lower()
    if algo not in digests:
        raise ModelInstallError("unsupported_checksum", f"Checksum algorithm '{algo}' is not supported")
    if digests[algo] != value.lower():
        raise ModelInstallError("checksum_mismatch", "The file does not match the checksum published by the source",
                                {"algorithm": algo, "expected": value.lower(), "actual": digests[algo]})


def install_file(src: Path, file_name: str) -> tuple[Path, dict[str, str], int]:
    """Copy into the content-addressed model store. Returns (path, digests, size)."""
    digests = file_digests(src)
    root = get_settings().model_dir.resolve()
    dest_dir = root / digests["sha256"]
    dest = dest_dir / file_name
    if not dest.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".install-")
        os.close(fd)
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o444)
        os.replace(tmp, dest)
    return dest, digests, dest.stat().st_size


def get_or_create_model(db: Session, *, slug: str, name: str, task: ModelTask, source: ModelSource,
                        concept_doi: str | None = None, description: str | None = None) -> RecognitionModel:
    m = db.scalar(select(RecognitionModel).where(RecognitionModel.slug == slug))
    if m is None:
        m = RecognitionModel(slug=slug, name=name, task=task, source=source, concept_doi=concept_doi, description=description)
        db.add(m)
        db.flush()
    return m


def register_file(db: Session, *, src: Path, slug: str, name: str, task: ModelTask, version: str,
                  source: ModelSource = ModelSource.MANUAL, file_name: str | None = None, doi: str | None = None,
                  record_id: str | None = None, license_id: str | None = None, source_checksum: str | None = None,
                  unicode_normalization: str | None = None, source_metadata: dict | None = None,
                  description: str | None = None) -> ModelVersion:
    if not src.is_file():
        raise ModelInstallError("file_not_found", f"No such file: {src}")
    m = get_or_create_model(db, slug=slug, name=name, task=task, source=source, description=description)
    if db.scalar(select(ModelVersion.id).where(ModelVersion.model_id == m.id, ModelVersion.version == version)):
        raise ModelInstallError("version_exists", f"{slug} {version} is already registered")
    from app.registry.manager import ensure_license

    ensure_license(db, license_id, "manual registration")
    mv = ModelVersion(model_id=m.id, version=version, doi=doi, record_id=record_id, file_name=file_name or src.name,
                      status=ModelStatus.INSTALLING, license_id=license_id, source_checksum=source_checksum,
                      unicode_normalization=unicode_normalization, source_metadata=source_metadata or {})
    log_event(mv, "installation started", source=str(src))
    digests = file_digests(src)
    verify_source_checksum(digests, source_checksum)
    if source_checksum:
        log_event(mv, "source checksum verified", checksum=source_checksum)
    dest, digests, size = install_file(src, mv.file_name)
    mv.file_path, mv.sha256, mv.size_bytes = str(dest), digests["sha256"], size
    mv.status, mv.installed_at = ModelStatus.INSTALLED, _now()
    log_event(mv, "file installed", path=str(dest), sha256=digests["sha256"], size=size)
    db.add(mv)
    db.flush()
    return mv


def validate(db: Session, mv: ModelVersion) -> ModelVersion:
    """Load the installed file with Kraken; VALIDATED on success, INCOMPATIBLE otherwise."""
    from app.recognition import engine

    task = mv.model.task
    try:
        desc = engine.describe_model(Path(mv.file_path))
        types = {t for n in desc["networks"] for t in n["model_type"]}
        if task.value not in types:
            raise engine.EngineError("wrong_model_type", f"File contains {sorted(types)} networks, expected {task.value}",
                                     "validate")
        actual = engine.sha256_file(Path(mv.file_path))
        if actual != mv.sha256:
            raise engine.EngineError("model_checksum_mismatch", "Installed file changed since installation", "validate")
    except engine.EngineError as e:
        mv.status = ModelStatus.INCOMPATIBLE
        log_event(mv, "validation failed", error=e.as_dict())
        return mv
    mv.file_metadata = desc
    mv.status = ModelStatus.VALIDATED
    mv.validated_at = _now()
    mv.kraken_version_validated = desc["kraken_version"]
    log_event(mv, "validated: model loaded by Kraken", kraken=desc["kraken_version"])
    if mv.model.active_version_id is None:
        mv.model.active_version_id = mv.id
    return mv


def register_bundled_segmentation(db: Session) -> ModelVersion:
    """Register Kraken's bundled baseline segmenter (blla) for the installed Kraken version."""
    from app.recognition import engine

    kv = engine.kraken_version()
    version = f"kraken-{kv}"
    m = get_or_create_model(db, slug="kraken-blla", name="Kraken default baseline segmenter (blla)",
                            task=ModelTask.SEGMENTATION, source=ModelSource.BUNDLED,
                            description="Baseline and region segmentation model distributed with Kraken.")
    existing = db.scalar(select(ModelVersion).where(ModelVersion.model_id == m.id, ModelVersion.version == version))
    if existing:
        return existing
    mv = register_file(db, src=engine.bundled_segmentation_model_path(), slug="kraken-blla", name=m.name,
                       task=ModelTask.SEGMENTATION, version=version, source=ModelSource.BUNDLED, file_name="blla.mlmodel",
                       license_id="Apache-2.0",
                       source_metadata={"distribution": f"kraken {kv} Python package", "license_source": "kraken package metadata"})
    return validate(db, mv)
