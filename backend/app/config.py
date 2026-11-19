"""Runtime configuration (12-factor, environment variables prefixed SCRIPTORIUM_)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-insecure-secret-change-me-0123456789abcdef"  # nosec B105 — development placeholder; production refuses to start with it (see _production_guards)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRIPTORIUM_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "SCRIPTORIUM"
    public_base_url: str = "http://localhost:3000"

    database_url: str = "postgresql+psycopg://scriptorium:scriptorium_dev@localhost:5432/scriptorium"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = _DEV_SECRET
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 14 * 24 * 3600
    password_reset_ttl_seconds: int = 3600
    cookie_secure: bool = False
    allow_self_registration: bool = True
    login_max_failures: int = 10
    login_lockout_seconds: int = 900

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: Path = Path("./var/storage")
    s3_endpoint_url: str | None = None
    s3_bucket: str = "scriptorium"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # Models
    model_dir: Path = Path("./var/models")
    # Training (Phase 6): working directories (data, checkpoints) and resource limits.
    training_dir: Path = Path("./var/training")
    training_threads: int = 2
    training_max_epochs: int = 200
    training_max_active_per_project: int = 1
    zenodo_api_url: str = "https://zenodo.org/api"
    zenodo_token: str | None = None

    # Uploads and ingestion
    max_upload_bytes: int = 512 * 1024 * 1024
    max_files_per_upload: int = 200
    max_image_pixels: int = 250_000_000  # decompression-bomb guard (≈ 15 800 × 15 800)
    max_pages_per_document: int = 2000
    pdf_render_dpi: int = 300

    # Recognition
    recognition_device: str = "cpu"  # cpu | gpu | auto
    max_active_jobs_per_user: int = 2000
    max_pages_per_request: int = 500
    job_soft_time_limit_seconds: int = 3600
    job_heartbeat_seconds: int = 15
    job_stale_after_seconds: int = 120  # PROCESSING job without heartbeat → worker lost → re-queued
    job_max_attempts: int = 3
    asset_url_ttl_seconds: int = 3600
    celery_eager: bool = False  # run tasks in-process (tests only)

    # Mail
    mail_backend: Literal["file", "smtp"] = "file"
    mail_outbox_dir: Path = Path("./var/outbox")
    mail_from: str = "SCRIPTORIUM <no-reply@localhost>"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True

    # Malware scanning (Phase 7): "clamd" streams every upload to an institution-operated ClamAV
    # daemon before storage; fail-closed when the daemon is unavailable.
    malware_scanner: Literal["none", "clamd"] = "none"
    clamd_address: str = "tcp://127.0.0.1:3310"  # or unix:///run/clamav/clamd.ctl
    malware_scan_timeout_seconds: int = 120

    # Retention (Phase 7): platform defaults; institutions may set stricter values in storage_policy.
    export_retention_days: int = 30          # batch export ZIPs are deleted after this many days (0 = keep)
    orphan_grace_days: int = 7               # unreferenced storage objects older than this are removed
    retention_interval_seconds: int = 24 * 3600

    # HTTP hardening (Phase 7)
    hsts_seconds: int = 0  # >0 emits Strict-Transport-Security (enable only when served over TLS)

    trust_forwarded_for: bool = False  # enable only behind a trusted reverse proxy
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key == _DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError("SCRIPTORIUM_SECRET_KEY must be set to a strong value (>=32 chars) in production")
            if not self.cookie_secure:
                raise ValueError("SCRIPTORIUM_COOKIE_SECURE must be true in production")
            if self.mail_backend == "file":
                raise ValueError("Use SCRIPTORIUM_MAIL_BACKEND=smtp in production")
            if "*" in self.cors_origins:
                raise ValueError("SCRIPTORIUM_CORS_ORIGINS must not contain '*' in production (credentials are sent)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
