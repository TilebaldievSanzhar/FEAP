"""Interval estimates for small benchmark samples."""
from __future__ import annotations

import math

_Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = _Z95) -> tuple[float, float] | None:
    """Wilson score interval for k successes out of n (95% by default).

    Unlike the normal approximation it stays inside [0, 1] and is usable at
    0/n and n/n — the usual case for rare outcomes such as silent errors.
    """
    if n <= 0:
        return None
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    # The exact bounds at 0/n and n/n are 0 and 1; floating point lands next to them.
    low = 0.0 if k == 0 else max(0.0, centre - half)
    high = 1.0 if k == n else min(1.0, centre + half)
    return low, high


def share(k: int, n: int) -> dict:
    interval = wilson(k, n)
    return {
        'k': k, 'n': n, 'share': k / n if n else None,
        'ci95': [round(interval[0], 4), round(interval[1], 4)] if interval else None,
    }
