"""Tests for the enqueue layer (ArqBackend + InProcessBackend).

ArqBackend just delegates to an arq pool — we use an AsyncMock to verify
the right arguments are sent. InProcessBackend spins coroutines locally;
we verify it tries to invoke the right task by patching the registry.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from feap.jobs.enqueue import ArqBackend, InProcessBackend, is_inprocess


async def test_arq_backend_enqueue_batch_uses_idempotency_key():
    pool = AsyncMock()
    backend = ArqBackend(pool)
    job_id = uuid.uuid4()

    await backend.enqueue_batch(job_id)

    pool.enqueue_job.assert_awaited_once_with(
        "run_classify_batch", str(job_id), _job_id=f"batch:{job_id}"
    )


async def test_arq_backend_enqueue_resume_serializes_answers():
    pool = AsyncMock()
    backend = ArqBackend(pool)
    item_id = uuid.uuid4()

    await backend.enqueue_resume_item(item_id, "session-1", {"power": "1.5 кВт"})

    pool.enqueue_job.assert_awaited_once()
    args, kwargs = pool.enqueue_job.call_args
    assert args[0] == "run_resume_item"
    assert args[1] == str(item_id)
    assert args[2] == "session-1"
    # Answers serialized as compact JSON with non-ASCII preserved.
    assert json.loads(args[3]) == {"power": "1.5 кВт"}
    assert kwargs["_job_id"] == f"resume:{item_id}"


async def test_inprocess_backend_spawns_named_task(monkeypatch):
    """InProcessBackend should dispatch to the named function in tasks.py."""
    backend = InProcessBackend()
    invoked = {"name": None, "args": None}

    async def fake_run(ctx: dict, *args):
        invoked["name"] = "run_classify_batch"
        invoked["args"] = args

    import feap.jobs.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "run_classify_batch", fake_run)

    job_id = uuid.uuid4()
    await backend.enqueue_batch(job_id)

    # Drain the spawned background task.
    while backend._tasks:
        for t in list(backend._tasks):
            await t

    assert invoked["name"] == "run_classify_batch"
    assert invoked["args"] == (str(job_id),)


def test_is_inprocess_reads_settings(monkeypatch):
    from feap import config

    monkeypatch.setattr(config.settings, "JOBS_BACKEND", "inprocess")
    assert is_inprocess() is True
    monkeypatch.setattr(config.settings, "JOBS_BACKEND", "arq")
    assert is_inprocess() is False
