# SCRIPTORIUM — Phase 1 Technical Report: Foundation

Date: 2026-11-17 · Status: **DELIVERED** · Schema revision: `c31e5a4077e0`

## 1. Environment inspection

| Item | Result |
|---|---|
| Host | Ubuntu 24.04.5, 2 vCPU, 7.8 GB RAM, no GPU |
| Python / Node | 3.12 (reference) / 22.23 |
| Kraken | **7.1.1 installed and working** (`kraken --version`), PyTorch 2.14 CPU; bundled segmentation model `blla.mlmodel` present |
| PostgreSQL / Redis | 16 / 7.0, native services |
| Docker | engine runs; **Docker Hub blocked** → images not buildable here |
| zenodo.org | blocked at session start; **reachable since 21:49** (HTTP 200 from the Zenodo API) — unblocks Phases 2–3 |
| Browser | Google Chrome → Playwright E2E |

Decisions derived from it are recorded in `docs/architecture.md`.

## 2. Architecture

Monorepo with independent layers: Next.js frontend → FastAPI REST API → PostgreSQL /
Redis / object storage; Celery workers host the Kraken engine (imported only by the
worker), the model registry and exports. See `docs/architecture.md`.

## 3. Implemented in Phase 1

**Backend (FastAPI, 36 REST operations incl. health, OpenAPI at `/api/docs`)**

* Authentication: registration (first account → platform admin), login, refresh-token
  rotation with reuse detection, logout, session listing/revocation, password reset by
  e-mail, password change, login throttling, account deactivation.
* Authorization: platform / institution / project roles (7 roles as specified), central
  permission engine, institution-level isolation, 404 for foreign resources.
* Institutions with members; research projects (independent or institutional, ISO 639-3
  languages, historical period, funding, archiving); project membership; collections with
  archival reference, holding institution and validated Dublin Core metadata.
* Append-only audit trail enforced by a PostgreSQL trigger; audit API with scoping.
* Dashboard: real `COUNT`s only; modules not yet delivered are listed as unavailable.
* Content-addressed, write-once object storage (local FS verified; S3/MinIO implemented).
* Celery application with `default`, `recognition`, `models` queues.
* Health: `/api/health/live`, `/api/health/ready` (database + schema revision, Redis, storage).
* Structured JSON logging with request id and user id; structured error responses.
* Production configuration guards.

**Database** — Alembic migration `c31e5a4077e0`: `users`, `institutions`,
`institution_memberships`, `user_sessions`, `password_reset_tokens`, `projects`,
`project_memberships`, `collections`, `audit_logs` (+ immutability trigger). Upgrade,
downgrade and re-upgrade verified.

**Frontend (Next.js 16, 13 routes)** — sign-in, registration, password reset request and
confirmation, dashboard, project list/search/archive, project detail (collections,
overview, members, activity), institutions and members, audit log, account (profile,
ORCID, language, password, sessions), user administration. English default, Portuguese
and French complete. Keyboard navigation, skip link, labelled controls. Runtime API proxy
(`SCRIPTORIUM_API_ORIGIN`). Self-hosted fonts.

**Deployment** — `deploy/Dockerfile.api` (no PyTorch), `Dockerfile.worker` (Kraken),
`Dockerfile.web` (standalone), `docker-compose.yml` (PostgreSQL, Redis, MinIO, migration
job, API, worker, web, health checks, named volumes), `.env.example`.
Scripts: `dev-up.sh`, `test-all.sh`, `e2e.sh`, `verify_persistence.py`.

## 4. Verification evidence

| Suite | Result | Evidence |
|---|---|---|
| Backend pytest (real PostgreSQL 16, Redis 7, real Celery worker process) | **41 passed**, coverage 93 % | `docs/evidence/phase1/test-run.log` |
| Frontend type-check + Vitest | **15 passed** | same log |
| End-to-end, Playwright + Google Chrome, real stack, isolated DB | **7 passed** | same log + screenshots `docs/evidence/phase1/*.png` |
| Persistence across restart of PostgreSQL, Redis and API | **PASS** | `docs/evidence/phase1/persistence.log` |
| Compose file validation | valid | `docker compose config` |

Selected behaviours proven by tests: argon2id hashes; identical errors for unknown user /
wrong password; lockout with `Retry-After`; rotated-token replay revokes the session
family and its access tokens; reset token single-use and expiring; institution isolation
(404); read-only and editor roles blocked from privileged actions; Dublin Core validation;
audit `UPDATE`/`DELETE` rejected by the database; audit survives project deletion;
dashboard counts equal SQL counts; HttpOnly refresh cookie scoped to `/api/v1/auth`;
session survives page reload; localisation PT/FR.

## 5. Defects found and corrected during the phase

1. Audit FKs with `ON DELETE SET NULL` would have updated audit rows (blocked by the
   immutability trigger) → audit columns made FK-free; migration regenerated.
2. Client IP not validated before writing to an `inet` column, and `X-Forwarded-For`
   trusted unconditionally (spoofable audit trail) → validation + opt-in proxy trust.
3. Next.js `rewrites` were frozen at build time (API origin not configurable in
   containers) → runtime proxy route handler.
4. Login page advertised Kraken/ALTO/PAGE/TEI features not yet operational → removed.
5. E2E runner left `next-server` processes alive between runs → process-group teardown.

## 6. Acceptance criteria touched by Phase 1

| Criterion | Status | Evidence |
|---|---|---|
| A. register and log in | **Met** | `test_auth.py`, E2E 1–3 |
| B. create a project | **Met** | `test_projects_permissions.py`, E2E 2 |
| Q. restart without data loss | **Met** (native services) | `persistence.log` |
| R. automated tests of essential workflows | **Met for Phase 1 scope** | test-run log |
| C–P | not in Phase 1 scope | — |

## 7. Not completed / limitations (explicit)

* Docker images not built or run (Docker Hub blocked in this environment); Compose is
  syntactically validated only. Must be smoke-tested on institutional infrastructure.
* S3/MinIO backend verified only against an in-process mock (`moto`, test marked
  `fixture_only`), not a real MinIO server.
* SMTP delivery path not exercised (file outbox used in tests).
* No institutional SSO (SAML/OIDC) yet; not in the master prompt's mandatory list, noted
  as a likely institutional requirement.
* Frontend dashboard shows Phase 1 metrics only; document/page/line/job/model counts arrive
  with their modules.

## 8. Next: Phase 2 — Kraken engine

Dependencies satisfied: Kraken 7.1.1 installed, bundled `blla` segmentation model
available, Celery queues in place, Zenodo now reachable (recognition models obtainable).
