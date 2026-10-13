"""Изоляция данных между организациями: задачи, OCR, сессии уточнений, документы."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from feap.api import jobs_router, ocr_router
from feap.auth.access import ensure_owner, job_owner, owner_matches
from feap.auth.deps import AuthContext
from feap.cascade import orchestrator as orch_module
from feap.cascade.orchestrator import CascadeOrchestrator, SessionNotFound
from feap.config import settings
from feap.dt import documents
from feap.dt.schemas import DocumentIn

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


def ctx(org_id: uuid.UUID) -> AuthContext:
    return AuthContext(org=SimpleNamespace(id=org_id, name="org"), role="owner")


class TestHelpers:
    def test_owner_matches(self):
        assert owner_matches(None, ORG_A)        # задачи до миграции 012
        assert owner_matches(ORG_A, ORG_A)
        assert not owner_matches(ORG_B, ORG_A)

    def test_ensure_owner_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            ensure_owner(ORG_B, ORG_A, "Не найдено")
        assert exc.value.status_code == 404

    def test_job_owner_falls_back_to_params(self):
        assert job_owner(SimpleNamespace(org_id=ORG_A, params={})) == ORG_A
        assert job_owner(SimpleNamespace(org_id=None, params={"org_id": str(ORG_B)})) == ORG_B
        assert job_owner(SimpleNamespace(org_id=None, params={"org_id": "garbage"})) is None


class TestJobsRouter:
    async def test_foreign_job_is_404(self, monkeypatch):
        async def get_job(db, job_id):
            return SimpleNamespace(org_id=ORG_B, params={})
        monkeypatch.setattr(jobs_router.repo, "get_job", get_job)
        with pytest.raises(HTTPException) as exc:
            await jobs_router._owned_job(None, uuid.uuid4(), ctx(ORG_A))
        assert exc.value.status_code == 404

    async def test_own_job_passes(self, monkeypatch):
        job = SimpleNamespace(org_id=ORG_A, params={})

        async def get_job(db, job_id):
            return job
        monkeypatch.setattr(jobs_router.repo, "get_job", get_job)
        assert await jobs_router._owned_job(None, uuid.uuid4(), ctx(ORG_A)) is job

    async def test_sse_with_token_checks_owner(self, monkeypatch):
        async def get_job(db, job_id):
            return SimpleNamespace(org_id=ORG_B, params={})

        async def fake_auth(db, creds, x_org_id):
            assert creds.credentials == "tok"
            return ctx(ORG_A)
        monkeypatch.setattr(jobs_router.repo, "get_job", get_job)
        monkeypatch.setattr(jobs_router, "get_auth_context", fake_auth)
        request = SimpleNamespace(headers={}, app=None)
        with pytest.raises(HTTPException) as exc:
            await jobs_router.stream_job_events(uuid.uuid4(), request, access_token="tok", db=None)
        assert exc.value.status_code == 404

    async def test_sse_anonymous_optional_mode_keeps_dev_behaviour(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MODE", "optional")

        async def must_not_check(*args, **kwargs):
            raise AssertionError("в анонимном dev-режиме владелец не проверяется")
        monkeypatch.setattr(jobs_router, "get_auth_context", must_not_check)
        monkeypatch.setattr("feap.api.deps.get_redis_client", lambda app: None)
        request = SimpleNamespace(headers={}, app=None)
        response = await jobs_router.stream_job_events(
            uuid.uuid4(), request, access_token=None, db=None,
        )
        assert response.media_type == "text/event-stream"


class TestOcrRouter:
    async def test_foreign_ocr_job_is_404(self, monkeypatch):
        async def get_job(db, ocr_job_id):
            return SimpleNamespace(org_id=ORG_B)
        monkeypatch.setattr(ocr_router.ocr_repo, "get_job", get_job)
        with pytest.raises(HTTPException) as exc:
            await ocr_router._owned_ocr_job(None, uuid.uuid4(), ctx(ORG_A))
        assert exc.value.status_code == 404


class TestClassificationSessions:
    async def test_resume_foreign_session_is_not_found(self, monkeypatch):
        async def load(db, sid):
            return SimpleNamespace(state={"product_description": "x"}, org_id=ORG_B)
        monkeypatch.setattr(orch_module, "load_session_row", load)
        with pytest.raises(SessionNotFound):
            await CascadeOrchestrator().resume("sid", {"a": "b"}, None, org_id=ORG_A)


class FakeSession:
    def __init__(self, job):
        self._job = job

    async def get(self, model, pk):
        return self._job


class TestDocumentFromOcr:
    async def test_foreign_ocr_job_cannot_be_attached(self):
        job = SimpleNamespace(org_id=ORG_B, extraction=[{"text": "Invoice", "filename": "a.pdf"}],
                              status="completed")
        payload = DocumentIn(ocr_job_id=uuid.uuid4())
        with pytest.raises(documents.DocumentSourceError):
            await documents._resolve_text(FakeSession(job), payload, ORG_A)

    async def test_own_ocr_job_is_attached(self):
        job = SimpleNamespace(org_id=ORG_A, extraction=[{"text": "Invoice", "filename": "a.pdf"}],
                              status="completed")
        payload = DocumentIn(ocr_job_id=uuid.uuid4())
        text, source = await documents._resolve_text(FakeSession(job), payload, ORG_A)
        assert text == "Invoice" and source["origin"] == "ocr"
