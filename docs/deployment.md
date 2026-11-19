# Production deployment

This guide covers an institutional deployment with Docker Compose (reference topology) and the
checks to run before opening the service to researchers. Native (non-container) installation for
development is described in [installation.md](installation.md).

> **Verification status.** The Compose file is validated with `docker compose config` and every
> service command is exercised natively by the test suites (API, recognition/models/training workers,
> scheduler, web, real ClamAV daemon). The images themselves could not be built in the development
> environment because Docker Hub is blocked there: build and smoke-test them in your infrastructure
> (checklist below) before production use.

## 1. Topology

```
                Internet / campus network
                          │ 443 (TLS)
                 ┌────────▼────────┐
                 │ proxy (Caddy) or │  HSTS, request size, timeouts
                 │ institutional LB │
                 └────────┬────────┘
                          │ 127.0.0.1:3000
                     ┌────▼────┐   /api/* proxied server-side
                     │   web   │──────────────┐
                     └─────────┘              │
                                         ┌────▼────┐     ┌─────────┐
                                         │   api   │────►│ clamav  │ INSTREAM scan before storage
                                         └────┬────┘     └─────────┘
              ┌───────────────┬───────────────┼───────────────┬───────────────┐
         ┌────▼────┐     ┌────▼─────┐   ┌─────▼─────┐   ┌─────▼──────┐  ┌─────▼─────┐
         │postgres │     │  redis   │   │   minio   │   │  workers   │  │ scheduler │
         │  (data) │     │ (queues) │   │ (objects) │   │ recognition│  │  (beat)   │
         └─────────┘     └──────────┘   └───────────┘   │ models     │  └───────────┘
                                                        │ training   │
                                                        └────────────┘
```

Only the proxy is reachable from outside; `web` listens on the loopback interface; every other
service is on the internal Compose network. Documents are never sent outside it (recognition runs on
the workers; the only outbound connections are the models worker → zenodo.org, freshclam →
ClamAV mirrors, and SMTP).

## 2. Sizing

| Service | CPU | RAM | Notes |
|---|---|---|---|
| api | 1–2 cores (one uvicorn process per core) | 1 GB | ≈ 37 req/s on 2 vCPU with the researcher mix (docs/performance.md) |
| worker (recognition) | 2+ cores per replica | 4–6 GB | ≈ 100 pages/hour per CPU worker; GPU strongly recommended for large campaigns |
| models-worker | 1 core | 2 GB | downloads and Kraken validation |
| training-worker | 4+ cores or a GPU | 4–8 GB | 2.1 GB peak measured for fine-tuning on CPU |
| clamav | 1 core | 1.5–3 GB | official signature databases are loaded in memory |
| postgres | 2 cores | 2–4 GB | 12–15 MB per installation in tests; grows with text and audit records |
| minio | — | 1 GB | storage ≈ originals + page images (PDF pages are rasterised at 300 dpi) |

## 3. Install

```bash
git clone … scriptorium && cd scriptorium
cp deploy/.env.example deploy/.env
# Generate every secret:  python3 -c "import secrets; print(secrets.token_urlsafe(48))"
#   SCRIPTORIUM_SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD, MINIO_ROOT_PASSWORD
# Set SCRIPTORIUM_PUBLIC_BASE_URL=https://<your host>, SCRIPTORIUM_DOMAIN=<your host>, SMTP settings.
chmod 600 deploy/.env

docker compose -f deploy/docker-compose.yml --env-file deploy/.env build
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile tls up -d   # with the bundled Caddy proxy
#   …or without --profile tls when your own reverse proxy terminates TLS (see §4)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
```

Startup order is enforced by health checks: PostgreSQL → `migrate` (Alembic, one-shot) → Redis,
MinIO and ClamAV healthy → API → web. ClamAV needs a few minutes on first start to download its
signatures; until then the API is not started (uploads must never be accepted unscanned).

GPU workers: build `deploy/Dockerfile.worker` with
`--build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu126`, add a device reservation to the
`worker`/`training-worker` services and set `SCRIPTORIUM_RECOGNITION_DEVICE=gpu`.

## 4. TLS and reverse proxy

**Bundled Caddy (`--profile tls`)** — obtains and renews certificates automatically for
`SCRIPTORIUM_DOMAIN` (DNS must point to the host, ports 80/443 open), sets HSTS, removes the
`Server` header, allows 2 GB request bodies and 30-minute timeouts (`deploy/Caddyfile`).

**Institutional nginx** — equivalent configuration:

```nginx
server {
    listen 443 ssl http2;
    server_name scriptorium.example.org;
    ssl_certificate     /etc/ssl/scriptorium/fullchain.pem;
    ssl_certificate_key /etc/ssl/scriptorium/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    client_max_body_size 2g;          # ≥ SCRIPTORIUM_MAX_UPLOAD_BYTES × files per upload
    proxy_read_timeout 1800s;
    proxy_send_timeout 1800s;
    proxy_request_buffering off;      # stream large uploads
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;   # overwrite, never append client values
        proxy_set_header X-Forwarded-Proto https;
    }
}
server { listen 80; server_name scriptorium.example.org; return 301 https://$host$request_uri; }
```

`SCRIPTORIUM_TRUST_FORWARDED_FOR=true` is set in the Compose file because the proxy is the only
route to the application; it must be `false` if clients could reach the API directly (otherwise
they could forge the IP addresses written to the audit trail).

## 5. First start

1. Open `https://<host>/register` and create the first account — it becomes the **platform
   administrator**. Self-registration is disabled by default afterwards in production
   (`SCRIPTORIUM_ALLOW_SELF_REGISTRATION=false`); administrators invite users (Administration → Users).
2. Create the institution(s), add institution administrators, set the **storage policy**
   (Institutions page; see [admin-guide.md](admin-guide.md)).
3. Install recognition models (Models → Install from Zenodo: TraPrInq, CATMuS Medieval;
   [models.md](models.md)). The worker registers Kraken's bundled segmenter itself at start.
4. Configure backups ([backup-recovery.md](backup-recovery.md)) and monitoring (§7).

## 6. Pre-production checklist

```bash
C="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
$C exec api python -m app.cli security-check        # environment=production, cookie_secure, clamd version…
curl -s https://<host>/api/health/ready | jq        # database, redis, storage, malware_scanner all "ok"
curl -sI https://<host>/login | grep -iE 'strict-transport|content-security|x-frame'
$C exec -T api python -m app.cli backup --dest /backups && $C exec api ls /backups   # then test a restore
# Upload the EICAR test file through the UI: it must be refused with "rejected by the malware scanner".
# Upload a real page, recognise it with TraPrInq, correct a line, export TEI: the end-to-end path.
```

The application refuses to start in production with the default or a short secret key, insecure
cookies, the file mail backend or a wildcard CORS origin.

## 7. Operations

* **Health**: `GET /api/health/live` (process) and `/api/health/ready` (database, Redis, storage,
  malware scanner; HTTP 503 when degraded). The containers have health checks; wire `/ready` into the
  institution's monitoring.
* **Logs**: JSON lines on stdout (`ts`, `level`, `logger`, `msg`, `request_id`, `user_id`, …);
  every response carries `X-Request-ID`, also returned in error bodies, to correlate reports with logs.
* **Scheduled tasks** (`scheduler` service, exactly one instance): stale-job recovery (60 s), model
  update checks (daily), data retention (daily, `SCRIPTORIUM_RETENTION_INTERVAL_SECONDS`).
* **Upgrades**: back up, pull the new release, `build`, `up -d` — the `migrate` service applies new
  Alembic revisions before the API starts. Model versions are never upgraded silently; administrators
  activate new versions explicitly.
* **Scaling**: add `worker` replicas for recognition throughput (each processes one page at a time);
  keep exactly one `scheduler`.
