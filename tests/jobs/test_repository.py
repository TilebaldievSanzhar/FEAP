"""Pure-Python repository helper tests.

The Postgres-dependent CRUD methods are exercised in integration
tests against a live database (`tests/integration/`, out of scope
for this unit suite). Here we just lock the helper functions that
have non-trivial logic and no DB dependency.
"""

from feap.jobs.repository import derive_job_status


def test_derive_status_handles_missing_keys():
    """Status histogram passed in may be sparse (missing keys for zero counts)."""
    assert derive_job_status({"completed": 1}) == "completed"
    assert derive_job_status({}) == "failed"  # no items at all → treat as failed-empty


def test_derive_status_running_overrides_everything():
    counts = {
        "pending": 0, "running": 1, "completed": 99,
        "failed": 5, "needs_input": 5, "cancelled": 5,
    }
    assert derive_job_status(counts) == "running"


def test_derive_status_pending_alone_is_running():
    """A freshly-created job (no items started yet) is still 'running' from the
    aggregator's view — it hasn't reached a terminal state."""
    assert derive_job_status({"pending": 5}) == "running"
