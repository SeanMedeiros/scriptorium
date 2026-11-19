"""Administrative command line.

    python -m app.cli register-bundled
    python -m app.cli register-model --file traprinq.mlmodel --slug traprinq --name "TraPrInq" \
        --task recognition --version V1 --doi 10.5281/zenodo.14543904 --license cc-by-4.0 \
        --checksum md5:b7f84ede75a0665c275346d94fdc1b95 --normalization NFD
    python -m app.cli list-models
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import session_factory
from app.models import ModelSource, ModelTask, ModelVersion
from app.registry import local


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scriptorium")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("register-bundled", help="register Kraken's bundled segmentation model")
    r = sub.add_parser("register-model", help="install and validate a local Kraken model file")
    r.add_argument("--file", required=True, type=Path)
    r.add_argument("--slug", required=True)
    r.add_argument("--name", required=True)
    r.add_argument("--task", choices=[t.value for t in ModelTask], required=True)
    r.add_argument("--version", required=True)
    r.add_argument("--doi")
    r.add_argument("--license")
    r.add_argument("--checksum", help="checksum published by the source, e.g. md5:…")
    r.add_argument("--normalization", choices=["NFC", "NFD", "NFKC", "NFKD"])
    r.add_argument("--metadata-json", type=Path, help="verbatim source metadata to store")
    sub.add_parser("list-models")
    sub.add_parser("recover-jobs", help="re-queue PROCESSING jobs whose worker was lost")
    rt = sub.add_parser("retention", help="apply data-retention policies (dry run unless --apply)")
    rt.add_argument("--apply", action="store_true")
    bk = sub.add_parser("backup", help="consistent, checksummed backup set (database, objects, models)")
    bk.add_argument("--dest", required=True, type=Path)
    rs = sub.add_parser("restore", help="verify a backup set and restore it into an EMPTY database")
    rs.add_argument("--src", required=True, type=Path)
    rs.add_argument("--model-dir", type=Path)
    vf = sub.add_parser("verify-backup", help="verify the checksums of a backup set without restoring")
    vf.add_argument("--src", required=True, type=Path)
    sub.add_parser("security-check", help="report security-relevant configuration and scanner reachability")
    args = ap.parse_args(argv)

    if args.cmd in ("backup", "restore", "verify-backup"):
        from app.ops import backup as bk_

        try:
            if args.cmd == "backup":
                out = bk_.backup(args.dest)
                print(json.dumps({"backup": str(out), **json.loads((out / "manifest.json").read_text())}, indent=2, default=str))
            elif args.cmd == "restore":
                print(json.dumps(bk_.restore(args.src, model_dir=args.model_dir), indent=2, default=str))
            else:
                print(json.dumps(bk_.verify_set(args.src)))
        except bk_.BackupError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            return 1
        return 0

    db = session_factory()()
    try:
        if args.cmd == "register-bundled":
            mv = local.register_bundled_segmentation(db)
            db.commit()
            print(json.dumps({"id": str(mv.id), "status": mv.status.value, "sha256": mv.sha256}))
        elif args.cmd == "register-model":
            meta = json.loads(args.metadata_json.read_text()) if args.metadata_json else {}
            mv = local.register_file(db, src=args.file, slug=args.slug, name=args.name, task=ModelTask(args.task),
                                     version=args.version, source=ModelSource.MANUAL, doi=args.doi, license_id=args.license,
                                     source_checksum=args.checksum, unicode_normalization=args.normalization,
                                     source_metadata=meta)
            db.commit()
            mv = local.validate(db, mv)
            db.commit()
            print(json.dumps({"id": str(mv.id), "status": mv.status.value, "sha256": mv.sha256, "log": mv.install_log},
                             indent=2))
            return 0 if mv.status.value == "validated" else 2
        elif args.cmd == "recover-jobs":
            from app.worker.pipeline import recover_stale_jobs

            print(json.dumps({"requeued": recover_stale_jobs()}))
        elif args.cmd == "retention":
            from app.services import retention

            print(json.dumps(retention.run(db, apply=args.apply), indent=2, default=str))
        elif args.cmd == "security-check":
            from app.api.admin import security_status
            from app.services import malware

            st = security_status(None)
            if st["malware_scanner"] == "clamd":
                try:
                    st["clamd"] = malware.ping()
                except malware.ScannerUnavailable as e:
                    st["clamd"] = f"UNAVAILABLE: {e}"
            print(json.dumps(st, indent=2))
        elif args.cmd == "list-models":
            for mv in db.scalars(select(ModelVersion)):
                print(f"{mv.id}  {mv.model.slug:20} {mv.version:16} {mv.status.value:12} {mv.sha256}")
    except local.ModelInstallError as e:
        db.rollback()
        print(json.dumps({"error": e.code, "message": e.message, "details": e.details}), file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
