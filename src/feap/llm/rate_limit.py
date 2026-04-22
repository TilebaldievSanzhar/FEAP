"""Rate limiting for LLM calls.

Two backends:
- LocalRateLimiter: per-process sliding window (asyncio).
- RedisSlidingWindowLimiter: distributed, Redis-backed (sliding window via ZSET).

Both expose the same async-context-manager protocol:

    async with limiter:
        await call_api(...)

`NullRateLimiter` is a no-op for cases where rate limiting is disabled.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def __aenter__(self) -> "RateLimiter": ...
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class NullRateLimiter:
    """No-op limiter (used when rate limiting is disabled)."""

    async def __aenter__(self) -> "NullRateLimiter":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class LocalRateLimiter:
    """Per-process sliding window rate limiter (RPM cap)."""

    def __init__(self, limit_per_min: int) -> None:
        if limit_per_min <= 0:
            raise ValueError("limit_per_min must be > 0")
        self._limit = limit_per_min
        self._window_s = 60.0
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "LocalRateLimiter":
        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - self._window_s
                while self._timestamps and self._timestamps[0] < cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return self
                wait_for = self._window_s - (now - self._timestamps[0]) + 0.01
            await asyncio.sleep(max(wait_for, 0.05))

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class RedisSlidingWindowLimiter:
    """Distributed RPM-cap limiter shared across all workers via Redis ZSET.

    Uses millisecond-precision timestamps as ZSET scores. Members are unique
    request tokens (uuid) so concurrent workers never collide.
    """

    def __init__(
        self,
        redis: Any,
        key: str,
        limit_per_min: int,
        window_s: float = 60.0,
    ) -> None:
        if limit_per_min <= 0:
            raise ValueError("limit_per_min must be > 0")
        self._redis = redis
        self._key = key
        self._limit = limit_per_min
        self._window_s = window_s

    async def __aenter__(self) -> "RedisSlidingWindowLimiter":
        import uuid as _uuid

        window_ms = int(self._window_s * 1000)
        while True:
            now_ms = int(time.time() * 1000)
            cutoff = now_ms - window_ms
            token = f"{now_ms}:{_uuid.uuid4().hex}"

            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(self._key, 0, cutoff)
            pipe.zcard(self._key)
            results = await pipe.execute()
            count = results[1]

            if count < self._limit:
                pipe2 = self._redis.pipeline()
                pipe2.zadd(self._key, {token: now_ms})
                pipe2.expire(self._key, int(self._window_s * 2))
                await pipe2.execute()
                return self

            # Sleep until the oldest element will fall out of window
            oldest = await self._redis.zrange(self._key, 0, 0, withscores=True)
            if oldest:
                _member, oldest_score = oldest[0]
                wait_ms = max(int(oldest_score) + window_ms - now_ms, 50)
            else:
                wait_ms = 100
            await asyncio.sleep(wait_ms / 1000.0)

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


def build_rate_limiter(
    *,
    limit_per_min: int,
    backend: str,
    redis: Any | None = None,
    key: str = "feap:llm:rpm",
) -> RateLimiter:
    """Build a rate limiter appropriate for the configured backend.

    - limit_per_min <= 0 → NullRateLimiter (disabled).
    - backend = "redis" with redis client → RedisSlidingWindowLimiter.
    - otherwise → LocalRateLimiter (per-process).
    """
    if limit_per_min <= 0:
        return NullRateLimiter()
    if backend == "redis" and redis is not None:
        return RedisSlidingWindowLimiter(redis, key, limit_per_min)
    if backend == "redis" and redis is None:
        logger.warning(
            "LLM_RATE_LIMITER_BACKEND=redis but no redis client provided; "
            "falling back to local limiter"
        )
    return LocalRateLimiter(limit_per_min)
