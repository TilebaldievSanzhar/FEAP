"""Redis pub/sub for live job events (used by SSE endpoint).

Channel naming: `feap:job:{job_id}` — one channel per job. Events are
JSON-serialized dicts with at minimum `{"event": str, ...}`. The SSE
endpoint subscribes to the channel and re-emits each message as an
SSE frame; heartbeats are emitted by the endpoint itself, not here.

Both publish and subscribe are no-ops when no Redis client is provided
(e.g. in-process backend or unit tests). The DB event log
(`classification_job_events`) is the persistent fallback.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


def channel_for(job_id: uuid.UUID) -> str:
    return f"feap:job:{job_id}"


async def publish(redis: Any, job_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Publish one event to the job's channel. Silently no-op without Redis."""
    if redis is None:
        return
    try:
        await redis.publish(channel_for(job_id), json.dumps(event, default=str))
    except Exception as exc:  # noqa: BLE001 — pub/sub is best-effort
        logger.warning("Failed to publish event for job %s: %s", job_id, exc)


async def subscribe(
    redis: Any, job_id: uuid.UUID
) -> AsyncIterator[dict[str, Any]]:
    """Yield events posted to the job's channel until the caller stops iterating.

    Caller is responsible for handling cancellation / heartbeats.
    """
    if redis is None:
        return
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_for(job_id))
    try:
        async for message in pubsub.listen():
            if message is None:
                continue
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Discarding non-JSON event payload for job %s", job_id)
    finally:
        try:
            await pubsub.unsubscribe(channel_for(job_id))
            await pubsub.close()
        except Exception:  # noqa: BLE001
            pass
