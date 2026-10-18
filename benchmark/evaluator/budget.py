"""Budget guard for paid benchmark runs.

v32_refs lost 35 of 100 cases to HTTP 402 and kept going, so the comparison it
produced could not be used. The guard:

1. estimates the cost of a run from a recorded trace and compares it with the
   OpenRouter balance before the start;
2. waits out 'in_flight_budget_exhausted' — it clears when requests in flight
   settle (OpenRouter sends Retry-After);
3. stops starting new cases when the 402 persists, leaving them for resume
   instead of recording them as failures.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

OPENROUTER_API = 'https://openrouter.ai/api/v1'

_RETRY_AFTER = re.compile(r"Retry-After['\"]?\s*:\s*['\"]?(\d+)", re.IGNORECASE)


def is_budget_error(message: str | None) -> bool:
    """OpenRouter 402: credits or in-flight budget exhausted."""
    if not message:
        return False
    return 'Error code: 402' in message or 'in_flight_budget' in message


def retry_after_s(message: str | None, default: int = 60) -> int:
    match = _RETRY_AFTER.search(message or '')
    return int(match.group(1)) if match else default


@dataclass(frozen=True)
class CostEstimate:
    n_cases: int
    per_case_mean_usd: float
    per_case_p90_usd: float
    estimate_usd: float
    reference: str

    def to_json(self) -> dict:
        return asdict(self)


def case_costs(trace_path: Path) -> dict[str, float]:
    """Recorded API cost per case id from an evaluate_audited trace."""
    costs: dict[str, float] = defaultdict(float)
    with trace_path.open(encoding='utf-8') as f:
        for line in f:
            record = json.loads(line)
            cost = (record.get('usage') or {}).get('cost') or 0
            costs[record.get('case_id') or '?'] += float(cost)
    return dict(costs)


def estimate_cost(trace_path: Path, n_cases: int, *, safety: float = 1.5) -> CostEstimate | None:
    """n × p90 cost per case × safety. None if the trace has no cost data."""
    costs = sorted(case_costs(trace_path).values())
    if not costs or not any(costs):
        return None
    p90 = costs[min(len(costs) - 1, math.ceil(0.9 * len(costs)) - 1)]
    return CostEstimate(
        n_cases=n_cases,
        per_case_mean_usd=round(sum(costs) / len(costs), 6),
        per_case_p90_usd=round(p90, 6),
        estimate_usd=round(n_cases * p90 * safety, 4),
        reference=trace_path.as_posix(),
    )


def latest_trace(results_dir: Path) -> Path | None:
    traces = [p for p in results_dir.rglob('*_trace.jsonl') if p.stat().st_size]
    return max(traces, key=lambda p: p.stat().st_mtime) if traces else None


async def openrouter_remaining_usd(api_key: str, *, timeout: float = 10.0) -> float | None:
    """Smallest known remainder: key limit, account credits. None if unknown."""
    if not api_key:
        return None
    values: list[float] = []
    async with httpx.AsyncClient(
        base_url=OPENROUTER_API, timeout=timeout,
        headers={'Authorization': f'Bearer {api_key}'},
    ) as client:
        for path in ('/key', '/credits'):
            try:
                response = await client.get(path)
                response.raise_for_status()
                data = response.json().get('data') or {}
            except (httpx.HTTPError, ValueError):
                continue
            if path == '/key' and data.get('limit_remaining') is not None:
                values.append(float(data['limit_remaining']))
            if path == '/credits' and data.get('total_credits') is not None:
                values.append(float(data['total_credits']) - float(data.get('total_usage') or 0))
    return min(values) if values else None
