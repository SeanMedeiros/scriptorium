# Administrator guide

Three administrative roles exist:

| Role | Scope | Typical tasks |
|---|---|---|
| **Platform administrator** | the whole installation | institutions, all accounts, model registry, retention runs, security status |
| **Institution administrator** | one institution | members, invitations, storage policy, institution report |
| **Project principal investigator** | one project | project members and roles, publication approval of trained models |

The first account registered on a new installation is the platform administrator. Deployment and
operations are covered in [deployment.md](deployment.md); models in [models.md](models.md);
backups in [backup-recovery.md](backup-recovery.md).

## Accounts

* **Invitations** — *Administration → Users → Invite user* (platform administrators) or
  *Institutions → Invite user* (institution administrators, for their institution). The account is
  created without a usable password and the person receives a single-use link, valid 7 days, to
  choose one. This is the normal way to create accounts when self-registration is disabled
  (`SCRIPTORIUM_ALLOW_SELF_REGISTRATION=false`, the production default).
* **Existing accounts** join an institution with *Add member* (e-mail + role) and a project with the
  project's *Members* tab (PI only).
* **Deactivation** (*Administration → Users*) revokes every session immediately; the account's work
  and audit trail remain. Accounts are never deleted, so authorship and provenance stay intact.
  Administrators cannot deactivate or demote themselves, and an institution always keeps at least one
  administrator.
* Users manage their own sessions and password under *Settings*; a password change or reset revokes
  their other sessions.

## Project roles

| Role | View | Upload | Run recognition | Transcribe | Review | Export | Train models | Manage members |
|---|---|---|---|---|---|---|---|---|
| Principal investigator | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Researcher | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| Transcription editor | ✓ | | | ✓ | | ✓ | | |
| Reviewer | ✓ | | | | ✓ | ✓ | | |
| Read-only researcher | ✓ | | | | | | | |

The API enforces these rules (`backend/app/core/permissions.py`); the interface only hides what a
role cannot do. Resources of other institutions are invisible (404).

## Storage policies and retention

Each institution has a storage policy (*Institutions → Storage policy*, institution administrators):

| Setting | Effect |
|---|---|
| Maximum file size | per-file upload limit for the institution's projects (≤ platform `SCRIPTORIUM_MAX_UPLOAD_BYTES`) |
| Storage quota for originals | uploads that would exceed it are refused (413 `storage_quota_exceeded`); usage counts distinct uploaded originals |
| Accepted upload types | any subset of PNG, JPEG, TIFF, PDF (415 `type_not_allowed_by_policy` otherwise) |
| Keep export archives (days) | batch-export ZIPs older than this are removed by the retention run (0 = keep); the export record, manifest summary and audit entry remain and researchers can regenerate the archive |
| Data-hosting statement | free text shown to members (e.g. where documents are hosted) |

Members see the policy in force; administrators also see usage computed from the database
(originals, documents, projects, export archives). Changes are audited with before/after values.
Projects of independent researchers use the platform defaults.

**Retention run** — daily (scheduler), or on demand:

```bash
python -m app.cli retention            # dry run: what would be removed
python -m app.cli retention --apply    # apply
curl -X POST -H "Authorization: Bearer $TOKEN" "$API/api/v1/admin/retention?apply=false"
```

It (1) expires export archives past their retention period, (2) deletes image-asset rows that no
document, page, segmentation, recognition or child asset references any more — i.e. files of
deleted documents — once older than `SCRIPTORIUM_ORPHAN_GRACE_DAYS` (default 7, the window to recover
an accidental deletion), and (3) deletes storage objects no database row references, after the same
grace period. Referenced research data, the immutable research record (recognitions, revisions,
ground truth, evaluation results) and the audit trail are never removed. Each applied run writes a
`retention.run` audit record with what it removed.

## Malware scanning

With `SCRIPTORIUM_MALWARE_SCANNER=clamd` (the Docker default) every uploaded document, model file
and ground-truth file is scanned by the ClamAV daemon before storage. Infected files are refused and
recorded (`upload.malware_detected` in the audit log, with the signature name). If the scanner is
down, uploads are refused (`malware_scanner_unavailable`) and `/api/health/ready` reports the outage
— restore the `clamav` service rather than disabling scanning. Keep freshclam updating signatures
(the `clamav/clamav` image does so automatically).

## Audit log

*Audit* (menu) lists every security- and research-relevant action: sign-ins, password changes,
uploads and refusals, recognition jobs, edits and reviews, exports and downloads, model
installations and activations, policy changes, invitations, retention runs. Platform administrators
see everything; institution administrators their institution; other users their projects and their
own actions. The table is append-only at database level; include it in backups.

## Security status

`GET /api/v1/admin/security-status` (platform administrators) and `python -m app.cli security-check`
report the security-relevant configuration — environment, secure cookies, HSTS, malware scanner and
its reachability, self-registration, forwarded-for trust, CORS origins, upload limit, retention
settings, storage backend and whether the development secret is still in use — without disclosing
secrets. Run it after every configuration change.

## Model administration (summary)

* Install from Zenodo (DOI, concept DOI, record URL or id) after reviewing metadata and licence;
  checksums are verified and Kraken loads the model before it becomes usable.
* Pin or roll back with *Activate*; updates are reported, never applied silently.
* Institutional models (uploaded or fine-tuned) stay inside their project; publication packages are
  produced only after PI approval and are never uploaded by the platform.

Details: [models.md](models.md) and [training.md](training.md).

## Reports

* **Dashboard** — counts from the database: projects, documents, pages, recognised lines, jobs by
  status, validated models, storage used, line review status, CER/WER from stored evaluations only.
* **Project report** (*Project → Report*, JSON/CSV) and **institution report**
  (`GET /api/v1/institutions/{id}/report`, institution administrators).
