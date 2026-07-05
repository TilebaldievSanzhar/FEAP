"""Rate-limiter tests using the local backend (no Redis needed).

The Redis-backed limiter is exercised in the integration test
`test_rate_limiter_redis` which is gated behind a fakeredis import.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from feap.llm.rate_limit import (
    LocalRateLimiter,
    NullRateLimiter,
    build_rate_limiter,
)


async def test_null_rate_limiter_is_noop():
    n = 0
    async with NullRateLimiter():
        n += 1
    assert n == 1


async def test_local_rate_limiter_allows_under_limit():
    limiter = LocalRateLimiter(limit_per_min=10)
    start = time.monotonic()
    for _ in range(5):
        async with limiter:
            pass
    # 5 acquires under a 10/min limit should be ~instant.
    assert time.monotonic() - start < 0.5


async def test_local_rate_limiter_serializes_burst():
    """Burst above the limit must wait until the window clears."""
    # 2 RPM = window of 60s; the third acquire should block.
    limiter = LocalRateLimiter(limit_per_min=2)
    # Shrink the internal window for a fast test.
    limiter._window_s = 0.3

    start = time.monotonic()
    async with limiter:
        pass
    async with limiter:
        pass
    async with limiter:
        # Third acquire forced to wait ~window_s.
        pass
    elapsed = time.monotonic() - start
    assert 0.25 <= elapsed < 1.0, f"expected ~0.3s, got {elapsed}"


async def test_build_rate_limiter_disabled_when_zero():
    limiter = build_rate_limiter(limit_per_min=0, backend="local")
    assert isinstance(limiter, NullRateLimiter)


async def test_build_rate_limiter_local_default():
    limiter = build_rate_limiter(limit_per_min=10, backend="local")
    assert isinstance(limiter, LocalRateLimiter)


async def test_build_rate_limiter_redis_falls_back_without_client():
    """If backend='redis' but no client provided, fall back to local."""
    limiter = build_rate_limiter(limit_per_min=10, backend="redis", redis=None)
    assert isinstance(limiter, LocalRateLimiter)


@pytest.mark.parametrize("concurrency", [1, 5])
async def test_local_rate_limiter_concurrent_acquires(concurrency: int):
    """Multiple coroutines competing — counts shouldn't get corrupted."""
    limiter = LocalRateLimiter(limit_per_min=concurrency * 100)

    counter = 0

    async def hit():
        nonlocal counter
        async with limiter:
            counter += 1

    await asyncio.gather(*(hit() for _ in range(20)))
    assert counter == 20
