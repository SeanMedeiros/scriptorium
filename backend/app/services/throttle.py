"""Login throttling backed by Redis (shared across API replicas)."""
from __future__ import annotations

from functools import lru_cache

import redis

from app.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def _key(identity: str) -> str:
    return f"scriptorium:login_fail:{identity.lower()}"


def is_locked(identity: str) -> int:
    """Return remaining lockout seconds (0 if not locked)."""
    s = get_settings()
    r = get_redis()
    fails = int(r.get(_key(identity)) or 0)
    if fails >= s.login_max_failures:
        return max(int(r.ttl(_key(identity))), 1)
    return 0


def register_failure(identity: str) -> None:
    s = get_settings()
    r = get_redis()
    pipe = r.pipeline()
    pipe.incr(_key(identity))
    pipe.expire(_key(identity), s.login_lockout_seconds)
    pipe.execute()


def reset(identity: str) -> None:
    get_redis().delete(_key(identity))
