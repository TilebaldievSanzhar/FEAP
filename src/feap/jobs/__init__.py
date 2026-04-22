"""Async job system for batch classification and OCR-derived workflows.

See docs/async_architecture.md for the full design rationale.

Surface:
- schemas.py: API request/response models for /classify/batch and /jobs/*
- repository.py: Postgres CRUD for jobs/items/events with atomic aggregates
- enqueue.py: high-level interface called from FastAPI
- tasks.py: arq task functions executed by workers
- worker.py: WorkerSettings consumed by `arq` CLI
- events.py: Redis pub/sub helpers for SSE streaming
- cron.py: cleanup / stuck-item recovery
- inprocess.py: local-dev fallback (JOBS_BACKEND=inprocess)
- rate_limit.py: see feap.llm.rate_limit (used by OpenRouterProvider)
"""
