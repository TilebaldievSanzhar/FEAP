"""Aggregation rules: item-status histogram → job status."""

from feap.jobs.repository import derive_job_status


def test_all_completed():
    assert derive_job_status({"completed": 5}) == "completed"


def test_any_pending_means_running():
    assert derive_job_status({"pending": 1, "completed": 4}) == "running"


def test_any_running_means_running():
    assert derive_job_status({"running": 1, "completed": 4}) == "running"


def test_needs_input_takes_priority_over_partial():
    assert derive_job_status(
        {"needs_input": 1, "completed": 3, "failed": 1}
    ) == "needs_input"


def test_partial_when_mixed_completed_and_failed():
    assert derive_job_status({"completed": 3, "failed": 2}) == "partial"


def test_partial_when_mixed_completed_and_cancelled():
    assert derive_job_status({"completed": 3, "cancelled": 1}) == "partial"


def test_failed_when_no_completed():
    assert derive_job_status({"failed": 5}) == "failed"


def test_failed_when_only_cancelled():
    # No successful items at all → caller gets a clear "everything failed" signal.
    assert derive_job_status({"cancelled": 3}) == "failed"


def test_running_beats_needs_input():
    # While items are still in flight we don't surface needs_input,
    # because it could resolve itself to completed.
    assert derive_job_status(
        {"running": 1, "needs_input": 2, "completed": 1}
    ) == "running"
