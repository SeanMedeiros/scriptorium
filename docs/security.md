# Security

Scope: the security controls implemented and tested in SCRIPTORIUM (Phases 1–7). Each phase section
lists what it added; the Phase 7 section records the production security review.

## Authentication

* Passwords: Argon2id (`argon2-cffi`), rehash on login when parameters change.
  Policy: 10–256 characters, not trivially simple (NIST SP 800-63B style; no forced
  composition rules).
* Access token: HS256 JWT, 15 minutes, held only in browser memory.
* Refresh token: 384-bit random, stored **as SHA-256 hash** in `user_sessions`, delivered
  as `HttpOnly; SameSite=Lax; Path=/api/v1/auth` cookie (`Secure` in production).
  Rotated on every refresh. **Replay of a rotated token revokes the entire session family**
  (theft detection).
* Access tokens are bound to their session family: logout or revocation is effective
  immediately, not at JWT expiry.
* Timing-equalised login (dummy hash for unknown accounts); identical error for unknown
  e-mail and wrong password.
* Brute-force protection: per-account failure counter in Redis (default 10 failures →
  15-minute lockout, `429` + `Retry-After`).
* Password reset: single-use token (hashed at rest, 60 minutes), identical response for
  existing and unknown addresses, all sessions revoked after reset.
* Password change revokes all other sessions; users can list and revoke sessions.
* Administrators cannot deactivate or demote themselves; deactivation revokes sessions.

## Authorization

Central rules in `backend/app/core/permissions.py`:

| Scope | Roles |
|---|---|
| Platform | platform administrator |
| Institution | institution administrator, member |
| Project | principal investigator, researcher, transcription editor, reviewer, read-only researcher |

* Every listing query is filtered through `visible_projects_query` → institution-level
  isolation in one place.
* Resources of other institutions answer **404**, not 403, so their existence is not
  disclosed.
* Institutional projects can only include members of that institution.
* An institution always keeps at least one administrator.

## Audit

* `audit_logs` is written in the same transaction as the change it records (actor, action,
  entity, institution, project, IP, user agent, request id, details).
* **Append-only enforced by PostgreSQL**: a trigger rejects every `UPDATE` and `DELETE`.
* No foreign keys from the audit trail to mutable entities, so deleting a project never
  rewrites history.
* `X-Forwarded-For` is ignored unless `SCRIPTORIUM_TRUST_FORWARDED_FOR=true`, preventing
  IP spoofing in the trail.

## Storage

* Content-addressed keys (SHA-256), objects written once, atomically (temp file + `link`),
  read-only file mode on disk; path traversal rejected.

## Transport and headers

* API: `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Request-ID`.
* Web: additionally `X-Frame-Options: DENY`, `Permissions-Policy`.
* TLS is terminated by the institutional reverse proxy; production refuses to start with
  `COOKIE_SECURE=false`.

## Configuration guards

Production mode refuses to start with the development secret key, a key shorter than 32
characters, insecure cookies or the file mail backend.

## Data sovereignty

No document or transcription is sent to third-party AI services. Fonts are self-hosted
(no font CDN). Recognition will run exclusively on institution-controlled Kraken workers.

## Phase 2 additions

* Upload validation by content signature (PNG/JPEG/TIFF/PDF), extension must agree;
  size limit enforced while streaming; Pillow decompression-bomb guard and an explicit
  pixel limit; page-count limit; malware-scanning integration point in `upload_documents`.
* Originals stored byte-for-byte (SHA-256 recorded, downloadable for verification).
* Images are served only through signed URLs that expire (default 1 h) and are issued to
  users with view permission; forged or expired signatures are rejected (403).
* Job submission requires the `run_recognition` permission; pages must belong to the
  project; per-user active-job quota and per-request page limit.
* Model files are verified against the source checksum before installation and against
  their SHA-256 before every load.

## Phase 4 additions

* Editing requires the `transcribe` project permission; marking lines reviewed requires `review`;
  read-only roles see the workspace without editing controls (enforced by the API, not the UI).
* Every revision, restore, policy change, annotation and manual segmentation is audited.
* Revisions and line versions are immutable at database level; a compromised client cannot
  rewrite history, only append to it.
* Markup and policy are validated server-side (HTTP 422 `invalid_edit`); the client-side
  validator is a convenience only.

## Phase 5 additions

* Ground-truth curation requires `review`; evaluation runs and comparisons `run_recognition`;
  combined transcriptions `transcribe`; exports and downloads `export`; reports and provenance `view`.
  Institution reports are restricted to institution administrators.
* Ground-truth imports: 25 MB limit, XML parsed without entity resolution or network access.
* Ground-truth snapshots and evaluation results are immutable (database triggers); withdrawals are
  separate records. Every curation, run, export request and download is audited.
* XML validation runs offline against vendored schemas (`backend/app/exports/schemas`, checksums in
  `SHA256SUMS`).

## Phase 6 additions

* Training, model upload and publication drafting require `train_models`; publication approval and
  DOI recording require the PI-level `manage_members`; weights/package download require `export`.
* Institutional models never leave their project scope (listing, direct access, jobs, downloads).
* Uploaded model files: extension check, 2 GB limit, SHA-256, loaded by Kraken on the isolated
  models worker before use. Model files are untrusted input: Kraken loads safetensors/CoreML formats
  (no pickle-based checkpoints are accepted for upload).
* Training runs in a separate process with its own working directory; cancellation is cooperative
  with a forced kill after 120 s.
* The platform never publishes to Zenodo: packages are produced only after recorded PI approval.

## Phase 7 — production security review

### Malware scanning (implemented, fail-closed)

* `SCRIPTORIUM_MALWARE_SCANNER=clamd` streams **every uploaded document, model file and ground-truth
  file** to an institution-operated ClamAV daemon (`INSTREAM` over TCP or a Unix socket,
  `SCRIPTORIUM_CLAMD_ADDRESS`) **before** anything is stored. No file leaves the institution.
* Detection → HTTP 422 `malware_detected` (signature name in the response), nothing stored,
  audit record `upload.malware_detected` written in its own transaction.
* clamd unreachable, timing out or answering with an error → HTTP 503 `malware_scanner_unavailable`;
  the upload is **refused, not accepted unscanned**, and audited (`upload.refused_scanner_unavailable`).
* `GET /api/health/ready` reports the scanner (engine version, or `disabled`), and turns
  `503 degraded` when a configured scanner is down. `python -m app.cli security-check` prints the
  security-relevant configuration and pings clamd.
* The Docker deployment runs `clamav/clamav` with freshclam-maintained official signatures and makes
  the API depend on its health check; the scanner is enabled by default there.
* Tests: `tests/test_phase7.py` uses a **real clamd (ClamAV 1.5.4)**. Because the official signature
  mirror was unreachable from the build environment, the test daemon (`scripts/clamd-test.sh`) loads
  `deploy/clamav/scriptorium-test.ndb`, a one-line body signature for the EICAR test string, in
  addition to any official databases present. Engine, protocol, detection and fail-closed behaviour
  are real; the E2E stack runs with the same daemon.
* Uploads remain size-limited while streaming, signature-sniffed (PNG/JPEG/TIFF/PDF), checked
  against the extension, pixel- and page-limited (decompression bombs), and parsed only after the
  scan. Ground-truth XML is parsed without entity resolution or network access.

### HTTP hardening

| Header | API (`/api/*`) | Web (Next.js) |
|---|---|---|
| Content-Security-Policy | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` (docs pages: self-hosted assets only) | `default-src 'self'`; `script-src 'self' 'unsafe-inline'` (Next.js bootstrap); `img-src 'self' data: blob:`; `connect-src 'self'`; `object-src 'none'`; `frame-ancestors 'none'` |
| X-Frame-Options | DENY | DENY |
| X-Content-Type-Options | nosniff | nosniff |
| Referrer-Policy | same-origin | same-origin |
| Cross-Origin-Opener-Policy | same-origin | same-origin |
| Strict-Transport-Security | when `SCRIPTORIUM_HSTS_SECONDS>0` | reverse proxy (Caddyfile sets 1 year) |
| Cache-Control: no-store | auth and `/users/me` endpoints | — |

* The interactive API documentation (`/api/docs`, `/api/redoc`) is served from vendored, checksummed
  assets (`backend/app/static/apidocs`, swagger-ui-dist 5.35.0 / redoc 2.5.4): **no CDN or font
  requests**, verified by the E2E suite which fails on any third-party request.
* Production refuses to start with a default/short secret, insecure cookies, the file mail backend,
  or a wildcard CORS origin.
* Known limitation: the web CSP allows inline scripts (required by the Next.js App Router without a
  nonce middleware). All other sources are restricted to the platform's own origin.

### Dependency and static analysis (evidence in `docs/evidence/phase7/`)

| Tool | Result |
|---|---|
| `pip-audit` (176 Python packages) | 1 finding: **lightning 2.6.1 — CVE-2026-58659 / GHSA-qqmf-gpg7-g8gw** (code execution when loading a crafted checkpoint). Fixed by installing lightning **2.6.6** over kraken 7.1.1's exact pin (`deploy/Dockerfile.worker`, `docs/installation.md`); the real Kraken recognition and fine-tuning suites pass with it. Exposure was already limited: uploads accept only `.mlmodel`/`.safetensors`, never Lightning checkpoints. |
| `npm audit` (frontend) | 0 vulnerabilities |
| `bandit -r backend/app` (≈10 k LOC) | 0 open findings after review. Fixed: MD5 used for Zenodo checksum comparison now `usedforsecurity=False`; `assert`-based control flow replaced by explicit errors; silent `except: pass` blocks now log or record diagnostics. Accepted with justification (`# nosec`): the development placeholder secret (rejected in production), the training runner subprocess (fixed argv, no shell). |
| `ruff --select S,ASYNC` | Blocking file I/O and socket scanning moved out of async handlers (model upload, ground-truth import now run in the threadpool). Remaining: two false-positive "hardcoded password" hits (`token_type="bearer"`, dev placeholder) and the reviewed subprocess call. |

### Data retention and storage policies

See [administrator guide](admin-guide.md#storage-policies-and-retention). Institution administrators
set per-file limits, accepted types, an originals quota and export-archive retention; the platform
retention run (scheduled daily, also `python -m app.cli retention [--apply]` and
`POST /api/v1/admin/retention`) expires export archives, removes asset rows that nothing references any
more (after document deletion) and deletes orphaned objects after a grace period. Every run is audited.
Referenced research data and the immutable research record are never removed by retention.

### Backups

Encrypted off-site copies are the institution's responsibility; `scripts/backup.sh` produces a
consistent, checksummed set (database, objects, models) and `scripts/restore.sh` verifies it before
restoring — see [backup and recovery](backup-recovery.md).
