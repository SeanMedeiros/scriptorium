# Installation

## 1. Local development (native services)

Verified on Ubuntu 24.04 (2 vCPU, 8 GB RAM, no GPU).

### Prerequisites

| Component | Version | Notes |
|---|---|---|
| Python | 3.12 | Kraken 7.1.1 supports 3.10–3.13; 3.12 is the reference |
| Node.js | 22 LTS | |
| PostgreSQL | 16 | |
| Redis | 7 | AOF persistence recommended |
| uv (optional) | ≥0.4 | faster installs |

Disk: the Python environment with PyTorch occupies ~6 GB.

### Steps

```bash
# 1. Python environment (API + worker + tests)
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -e "backend[worker,test]"
# security fix over kraken's exact pin (CVE-2026-58659, see docs/security.md)
uv pip install -p .venv/bin/python --no-deps "lightning==2.6.6"
.venv/bin/kraken --version            # → kraken, version 7.1.1

# 2. Database
sudo -u postgres psql \
  -c "CREATE ROLE scriptorium LOGIN PASSWORD 'scriptorium_dev' CREATEDB" \
  -c "CREATE DATABASE scriptorium OWNER scriptorium" \
  -c "CREATE DATABASE scriptorium_test OWNER scriptorium"

# 3. Configuration
cp backend/.env.example backend/.env   # edit as needed (all keys: backend/app/config.py)

# 4. Frontend
(cd frontend && npm ci)

# 5. Start everything (runs migrations first)
scripts/dev-up.sh          # production build of the web app
scripts/dev-up.sh --dev    # hot-reloading web app
```

Services: web http://localhost:3000 · API http://localhost:8000 · OpenAPI http://localhost:8000/api/docs ·
readiness http://localhost:8000/api/health/ready.

The first registered account becomes **platform administrator**. Afterwards set
`SCRIPTORIUM_ALLOW_SELF_REGISTRATION=false` if accounts must be created by administrators.

### Configuration reference

All settings are environment variables prefixed `SCRIPTORIUM_` (or entries in `backend/.env`).

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | development | `production` enables strict checks (secret, secure cookies, SMTP) |
| `DATABASE_URL` | local PostgreSQL | SQLAlchemy URL (`postgresql+psycopg://…`) |
| `REDIS_URL` | redis://localhost:6379/0 | Celery broker/backend, login throttling |
| `SECRET_KEY` | dev value | JWT signing key (≥32 chars in production) |
| `STORAGE_BACKEND` | local | `local` or `s3` |
| `STORAGE_LOCAL_ROOT` | ./var/storage | local object store |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | — | MinIO/S3 |
| `MODEL_DIR` | ./var/models | installed recognition models |
| `MAIL_BACKEND` | file | `file` (writes .eml to `MAIL_OUTBOX_DIR`) or `smtp` |
| `SMTP_*` | — | SMTP server |
| `PUBLIC_BASE_URL` | http://localhost:3000 | used in e-mailed links |
| `COOKIE_SECURE` | false | must be true behind HTTPS |
| `TRUST_FORWARDED_FOR` | false | honour `X-Forwarded-For` (only behind a trusted proxy) |
| `ALLOW_SELF_REGISTRATION` | true | public registration |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_SECONDS` | 10 / 900 | brute-force protection |

Frontend: `SCRIPTORIUM_API_ORIGIN` (default `http://127.0.0.1:8000`) — read at request time.

## 2. Docker Compose (institutional deployment)

```bash
cp deploy/.env.example deploy/.env      # replace every secret
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.yml ps
```

Services: `postgres`, `redis`, `minio`, `clamav`, `migrate` (one-shot Alembic), `api`,
`worker` (Kraken recognition), `models-worker`, `training-worker`, `scheduler`, `web`, and the
optional `proxy` (Caddy, `--profile tls`). Named volumes hold all persistent data. The full
production procedure is in [deployment.md](deployment.md).

> **Verification status:** the Compose file is validated (`docker compose config`),
> but the images could not be built in the development environment because Docker Hub
> is blocked there. Build and smoke-test them in your infrastructure before production.

GPU workers: build `deploy/Dockerfile.worker` with
`--build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu126` and expose the GPU to the
container.

### Optional: malware scanning in development

```bash
sudo apt-get install clamav-daemon     # ClamAV 1.x
scripts/clamd-test.sh                  # clamd on 127.0.0.1:3310 (official DBs if present + EICAR test signature)
export SCRIPTORIUM_MALWARE_SCANNER=clamd
```

`scripts/e2e.sh` does this automatically when `clamd` is installed.

## 3. Running the tests

```bash
scripts/test-all.sh
```

* Backend tests need PostgreSQL (`scriptorium_test`, dropped and recreated by the suite) and
  Redis (db 15, flushed).
* End-to-end tests create an isolated `scriptorium_e2e` database, start the API on :8001 and
  the web app on :3001, and drive Google Chrome through Playwright
  (`E2E_BROWSER_CHANNEL=chromium` to use Playwright's bundled Chromium instead).

## 4. Network access required

| Host | Used for | Phase |
|---|---|---|
| pypi.org, files.pythonhosted.org | Python packages | install |
| registry.npmjs.org | Node packages | install |
| zenodo.org | model metadata and model files | 3 |
