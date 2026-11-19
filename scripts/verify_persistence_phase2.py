#!/usr/bin/env python3
"""Phase 2/4 persistence check: recognition outputs, stored images and installed models
survive a restart of PostgreSQL and Redis.

Usage: verify_persistence_phase2.py <database-url>   (e.g. the E2E database after scripts/e2e.sh)
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
QUERIES = {
    "documents": "SELECT count(*) FROM documents",
    "pages": "SELECT count(*) FROM pages",
    "image_assets": "SELECT count(*) FROM image_assets",
    "jobs_completed": "SELECT count(*) FROM recognition_jobs WHERE status = 'completed'",
    "segmentations": "SELECT count(*) FROM segmentations",
    "text_lines": "SELECT count(*) FROM text_lines",
    "recognitions": "SELECT count(*) FROM recognitions",
    "line_recognitions": "SELECT count(*) FROM line_recognitions",
    "recognised_text_md5": "SELECT md5(string_agg(text, E'\\n' ORDER BY recognition_id, order_index)) FROM line_recognitions",
    "validated_models": "SELECT count(*) FROM model_versions WHERE status = 'validated'",
    # Phase 4: transcriptions, immutable revision chain and line versions
    "transcriptions": "SELECT count(*) FROM transcriptions",
    "revisions": "SELECT count(*) FROM transcription_revisions",
    "line_versions": "SELECT count(*) FROM line_versions",
    "line_versions_md5": "SELECT md5(string_agg(coalesce(text,'') || '|' || coalesce(normalized_text,'') || '|' || coalesce(note,'') || '|' || status::text, E'\\n' ORDER BY id)) FROM line_versions",
    "annotations": "SELECT count(*) FROM annotations",
    # Phase 5: ground truth, evaluation results, comparisons, exports
    "ground_truth_pages": "SELECT count(*) FROM ground_truth_pages",
    "ground_truth_text_md5": "SELECT md5(string_agg(text, E'\\n' ORDER BY gt_page_id, order_index)) FROM ground_truth_lines",
    "evaluation_results": "SELECT count(*) FROM evaluation_results",
    "evaluation_cer": "SELECT string_agg(round(cer::numeric, 6)::text, ',' ORDER BY id) FROM evaluation_results",
    "comparisons": "SELECT count(*) FROM comparisons",
    "exports_completed": "SELECT count(*) FROM exports WHERE status = 'completed'",
    "combined_transcriptions": "SELECT count(*) FROM transcriptions WHERE origin = 'combined'",
    # Phase 6: training jobs, trained/institutional models, publications
    "training_jobs_completed": "SELECT count(*) FROM training_jobs WHERE status = 'completed'",
    "training_epochs": "SELECT string_agg(epochs::text, ';' ORDER BY id) FROM training_jobs",
    "trained_model_versions": "SELECT count(*) FROM model_versions WHERE training_job_id IS NOT NULL AND status = 'validated'",
    "publications_approved": "SELECT count(*) FROM model_publications WHERE approved_at IS NOT NULL",
}


def snapshot(url: str) -> tuple[dict, list[tuple[str, str]], list[tuple[str, str]]]:
    with psycopg.connect(url) as c:
        counts = {k: c.execute(q).fetchone()[0] for k, q in QUERIES.items()}
        assets = c.execute("SELECT storage_key, sha256 FROM image_assets").fetchall()
        models = c.execute("SELECT file_path, sha256 FROM model_versions WHERE file_path IS NOT NULL").fetchall()
    return counts, assets, models


def verify_files(storage_root: Path, assets, models) -> None:
    for key, sha in assets:
        assert hashlib.sha256((storage_root / key).read_bytes()).hexdigest() == sha, key
    for path, sha in models:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while b := f.read(1 << 20):
                h.update(b)
        assert h.hexdigest() == sha, path


def main() -> int:
    url = sys.argv[1].replace("postgresql+psycopg://", "postgresql://")
    storage_root = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "var/e2e/storage"
    before, assets, models = snapshot(url)
    print(f"[{time.strftime('%H:%M:%S')}] before restart: {before}")
    verify_files(storage_root, assets, models)
    print(f"[{time.strftime('%H:%M:%S')}] {len(assets)} stored images and {len(models)} model files match their SHA-256")
    subprocess.run(["pg_ctlcluster", "16", "main", "restart"], check=True)
    subprocess.run(["redis-cli", "shutdown", "save"], check=False)
    time.sleep(1)
    subprocess.run(["redis-server", "--daemonize", "yes", "--appendonly", "yes", "--dir", "/var/lib/redis"], check=True)
    time.sleep(2)
    print(f"[{time.strftime('%H:%M:%S')}] PostgreSQL 16 and Redis restarted")
    after, assets2, models2 = snapshot(url)
    print(f"[{time.strftime('%H:%M:%S')}] after restart:  {after}")
    assert after == before and assets2 == assets and models2 == models
    verify_files(storage_root, assets2, models2)
    print(f"[{time.strftime('%H:%M:%S')}] RESULT: PASS — recognition outputs, images and models intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
