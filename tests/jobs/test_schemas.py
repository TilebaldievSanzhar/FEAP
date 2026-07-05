"""Smoke tests for jobs API schemas — catch obvious typos & required fields."""

import uuid

import pytest
from pydantic import ValidationError

from feap.jobs.schemas import (
    BatchAsyncResponse,
    BatchItemInput,
    ClassifyBatchRequest,
    JobAnswerEntry,
    JobAnswersBatchRequest,
    JobProgress,
    JobResponse,
)


def test_classify_batch_request_requires_at_least_one_item():
    with pytest.raises(ValidationError):
        ClassifyBatchRequest(items=[], mode="async")


def test_classify_batch_request_default_mode():
    req = ClassifyBatchRequest(items=[BatchItemInput(description="x")])
    assert req.mode == "auto"


def test_classify_batch_request_rejects_empty_description():
    with pytest.raises(ValidationError):
        ClassifyBatchRequest(items=[BatchItemInput(description="")])


def test_batch_async_response_serializes_uuid():
    job_id = uuid.uuid4()
    resp = BatchAsyncResponse(
        job_id=job_id,
        status="pending",
        total_items=3,
        poll_url="/api/v1/jobs/x",
        items_url="/api/v1/jobs/x/items",
        events_url="/api/v1/jobs/x/events",
    )
    payload = resp.model_dump(mode="json")
    assert payload["job_id"] == str(job_id)
    assert payload["total_items"] == 3


def test_job_progress_round_trip():
    progress = JobProgress(
        total=10, pending=2, running=3, completed=4,
        failed=0, needs_input=1, cancelled=0,
    )
    assert progress.total == 10
    assert progress.pending + progress.running + progress.completed
    assert progress.needs_input == 1


def test_job_answers_batch_requires_entries():
    with pytest.raises(ValidationError):
        JobAnswersBatchRequest(answers=[])


def test_job_answer_entry_round_trip():
    entry = JobAnswerEntry(
        item_id=uuid.uuid4(),
        answers={"power": "1.5 кВт"},
    )
    assert entry.answers["power"] == "1.5 кВт"
