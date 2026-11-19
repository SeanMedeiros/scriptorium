"""Backup and restore (Phase 7).

A backup set is a directory::

    scriptorium-backup-<UTC timestamp>/
        database.dump          pg_dump custom format (schema + data, no owner/ACL statements)
        objects/<key>          every object of the configured storage backend (local or S3)
        models/<sha256>/<file> every model file referenced by the database
        manifest.json          versions, schema revision, counts, research-data fingerprint
        SHA256SUMS             checksum of every file above (sha256sum -c compatible)

Consistency: the database is dumped first (a single MVCC snapshot); storage objects are immutable
and content-addressed, so every object the dump references already exists when objects are copied
afterwards. Each object is verified against the SHA-256 embedded in its key while it is copied.
Retention runs must not overlap a backup (they only delete unreferenced data, but the backup should
not reference objects deleted mid-copy).

Restore verifies SHA256SUMS **before** touching anything, refuses a non-empty target database,
restores the dump, writes objects through the storage backend (keys must come out identical),
installs model files and rewrites their paths, then verifies every image asset and model file
against the hashes recorded in the restored database and compares the research-data fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg

from app import __version__
from app.config import get_settings
from app.storage import get_storage

CHUNK = 1 << 20

# Research-data fingerprint: counts and content hashes that must be identical after a restore.
FINGERPRINT = {
    "users": "SELECT count(*) FROM users",
    "institutions": "SELECT count(*) FROM institutions",
    "projects": "SELECT count(*) FROM projects",
    "documents": "SELECT count(*) FROM documents",
    "pages": "SELECT count(*) FROM pages",
    "image_assets": "SELECT count(*) FROM image_assets",
    "image_assets_sha256": "SELECT md5(string_agg(sha256, ',' ORDER BY id)) FROM image_assets",
    "recognitions": "SELECT count(*) FROM recognitions",
    "recognised_text_md5": "SELECT md5(string_agg(text, E'\\n' ORDER BY recognition_id, order_index)) FROM line_recognitions",
    "transcription_revisions": "SELECT count(*) FROM transcription_revisions",
    "line_versions_md5": "SELECT md5(string_agg(coalesce(text,'') || '|' || status::text, E'\\n' ORDER BY id)) FROM line_versions",
    "ground_truth_text_md5": "SELECT md5(string_agg(text, E'\\n' ORDER BY gt_page_id, order_index)) FROM ground_truth_lines",
    "evaluation_cer": "SELECT string_agg(round(cer::numeric, 6)::text, ',' ORDER BY id) FROM evaluation_results",
    "model_versions": "SELECT string_agg(coalesce(sha256,'-') || ':' || status::text, ',' ORDER BY id) FROM model_versions",
    "training_jobs": "SELECT count(*) FROM training_jobs",
    "audit_logs": "SELECT count(*) FROM audit_logs",
}


class BackupError(Exception):
    pass


def _libpq(url: str) -> tuple[str, dict]:
    """SQLAlchemy URL → (libpq URI without password, env with PGPASSWORD)."""
    u = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    netloc = (f"{unquote(u.username)}@" if u.username else "") + (u.hostname or "localhost") + (f":{u.port}" if u.port else "")
    return f"postgresql://{netloc}{u.path}", env


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while b := f.read(CHUNK):
            h.update(b)
    return h.hexdigest()


def fingerprint(database_url: str) -> dict:
    uri, env = _libpq(database_url)
    out = {}
    with psycopg.connect(uri, password=env.get("PGPASSWORD")) as c:
        for k, q in FINGERPRINT.items():
            out[k] = c.execute(q).fetchone()[0]
        out["schema_revision"] = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return out


def backup(dest_root: Path, *, database_url: str | None = None) -> Path:
    s = get_settings()
    db_url = database_url or s.database_url
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_root / f"scriptorium-backup-{stamp}"
    (dest / "objects").mkdir(parents=True)
    (dest / "models").mkdir()
    uri, env = _libpq(db_url)

    # 1. Database (single consistent snapshot).
    r = subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dest / "database.dump"), uri],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise BackupError(f"pg_dump failed: {r.stderr.strip()[:1000]}")
    fp = fingerprint(db_url)

    # 2. Storage objects, verified against their content address while copying.
    storage = get_storage()
    n_obj = obj_bytes = 0
    for key, _size, _mtime in storage.iter_objects():
        target = dest / "objects" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        with target.open("wb") as out:
            for chunk in storage.iter_bytes(key):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != key.rsplit("/", 1)[-1]:
            raise BackupError(f"object {key} does not match its content address (corrupted storage?)")
        n_obj += 1
        obj_bytes += target.stat().st_size

    # 3. Model files referenced by the database.
    models = []
    with psycopg.connect(uri, password=env.get("PGPASSWORD")) as c:
        rows = c.execute("SELECT id::text, file_path, sha256 FROM model_versions WHERE file_path IS NOT NULL").fetchall()
    for mv_id, fpath, sha in rows:
        src = Path(fpath)
        if not src.is_file():
            models.append({"id": mv_id, "path": fpath, "missing": True})
            continue
        rel = Path(sha or _sha256_file(src)) / src.name
        (dest / "models" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / "models" / rel)
        models.append({"id": mv_id, "path": fpath, "backup_path": f"models/{rel.as_posix()}", "sha256": sha})

    manifest = {
        "format": "scriptorium-backup/1", "created_at": datetime.now(timezone.utc).isoformat(),
        "scriptorium_version": __version__, "schema_revision": fp["schema_revision"],
        "storage_backend": storage.name, "objects": n_obj, "object_bytes": obj_bytes,
        "models": models, "fingerprint": fp,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    # 4. Checksums of every file in the set.
    lines = [f"{_sha256_file(p)}  {p.relative_to(dest).as_posix()}"
             for p in sorted(dest.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
    (dest / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    return dest


def verify_set(src: Path) -> dict:
    sums = src / "SHA256SUMS"
    if not sums.is_file():
        raise BackupError("SHA256SUMS missing — not a SCRIPTORIUM backup set")
    bad, n = [], 0
    for line in sums.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        p = src / rel
        n += 1
        if not p.is_file() or _sha256_file(p) != digest:
            bad.append(rel)
    if bad:
        raise BackupError(f"{len(bad)} file(s) fail checksum verification: {bad[:10]}")
    return {"files_verified": n}


def restore(src: Path, *, database_url: str | None = None, model_dir: Path | None = None) -> dict:
    s = get_settings()
    db_url = database_url or s.database_url
    model_root = (model_dir or s.model_dir).resolve()
    report: dict = {"source": str(src)}
    report.update(verify_set(src))  # nothing is touched before the whole set verifies
    manifest = json.loads((src / "manifest.json").read_text())
    uri, env = _libpq(db_url)

    with psycopg.connect(uri, password=env.get("PGPASSWORD")) as c:
        n = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
    if n:
        raise BackupError("target database is not empty; restore into a newly created database")
    r = subprocess.run(["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "--dbname", uri, str(src / "database.dump")],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise BackupError(f"pg_restore failed: {r.stderr.strip()[:1000]}")

    storage = get_storage()
    restored = 0
    for p in sorted((src / "objects").rglob("*")):
        if not p.is_file():
            continue
        key = p.relative_to(src / "objects").as_posix()
        namespace = key.rsplit("/", 2)[0]
        with p.open("rb") as f:
            obj = storage.put_stream(namespace, f)
        if obj.key != key:
            raise BackupError(f"restored object key mismatch: {key} → {obj.key}")
        restored += 1
    report["objects_restored"] = restored

    with psycopg.connect(uri, password=env.get("PGPASSWORD")) as c:
        for m in manifest["models"]:
            if m.get("missing"):
                continue
            src_file = src / m["backup_path"]
            target = model_root / Path(m["backup_path"]).relative_to("models")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(src_file, target)
            c.execute("UPDATE model_versions SET file_path=%s WHERE id=%s", (str(target), m["id"]))
        c.commit()
    report["models_restored"] = sum(1 for m in manifest["models"] if not m.get("missing"))
    report.update(verify_restored(db_url, model_root))
    after = fingerprint(db_url)
    diff = {k: (manifest["fingerprint"].get(k), after.get(k)) for k in after if str(manifest["fingerprint"].get(k)) != str(after.get(k))}
    report["fingerprint_identical"] = not diff
    report["fingerprint_differences"] = diff
    if diff:
        raise BackupError(f"restored database differs from the backup fingerprint: {diff}")
    return report


def verify_restored(database_url: str, model_root: Path | None = None) -> dict:
    """Every image asset and installed model file present and matching the hash in the database."""
    uri, env = _libpq(database_url)
    storage = get_storage()
    missing, corrupt = [], []
    with psycopg.connect(uri, password=env.get("PGPASSWORD")) as c:
        assets = c.execute("SELECT storage_key, sha256 FROM image_assets").fetchall()
        models = c.execute("SELECT file_path, sha256 FROM model_versions WHERE file_path IS NOT NULL").fetchall()
    for key, sha in assets:
        if not storage.exists(key):
            missing.append(key)
            continue
        h = hashlib.sha256()
        for chunk in storage.iter_bytes(key):
            h.update(chunk)
        if h.hexdigest() != sha:
            corrupt.append(key)
    model_bad = [p for p, sha in models if not Path(p).is_file() or (sha and _sha256_file(Path(p)) != sha)]
    if missing or corrupt or model_bad:
        raise BackupError(f"verification failed: missing={missing[:5]} corrupt={corrupt[:5]} models={model_bad[:5]}")
    return {"assets_verified": len(assets), "models_verified": len(models)}
