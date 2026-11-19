"""Short-lived signed URLs for stored assets (images cannot carry an Authorization header)."""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from app.config import get_settings


def _sig(asset_id: uuid.UUID, exp: int) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"asset:{asset_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:40]


def signed_url(asset_id: uuid.UUID, ttl: int | None = None) -> str:
    ttl = ttl or get_settings().asset_url_ttl_seconds
    # Round expiry to the TTL window so URLs are cacheable by the browser.
    exp = (int(time.time()) // ttl + 2) * ttl
    return f"/api/v1/assets/{asset_id}/content?exp={exp}&sig={_sig(asset_id, exp)}"


def verify(asset_id: uuid.UUID, exp: int, sig: str) -> bool:
    return exp >= int(time.time()) and hmac.compare_digest(_sig(asset_id, exp), sig)
