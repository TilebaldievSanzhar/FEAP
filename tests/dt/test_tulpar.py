"""TULPAR: формат номера, кэш, persist, отключённый реестр."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest

from feap.config import settings
from feap.dt import tulpar
from feap.dt.tulpar import (
    PermitCheckResult,
    PermitKindError,
    check_number_format,
    verify_permit,
)


class TestNumberFormat:
    @pytest.mark.parametrize("number,ok", [
        ("KG417/005.02", True),
        ("ЕАЭС N RU Д-CN.АБ12.В.00001/26", True),
        (None, False),
        ("", False),
        ("12", False),                    # слишком короткий
        ("БЕЗЦИФР-НОМЕР", False),         # нет цифр
        ("A1\nB2C3", False),              # перенос строки
        ("X" * 70 + "1", False),          # слишком длинный
    ])
    def test_cases(self, number, ok):
        result, detail = check_number_format(number)
        assert result is ok, detail


class FakeSession:
    """Минимум для verify_permit: get + flush."""

    def __init__(self, row):
        self._row = row
        self.flushed = False

    async def get(self, _model, _pk):
        return self._row

    async def flush(self):
        self.flushed = True


def _doc_row(kind: str = "permit", number: str | None = "KG417/005.02",
             extracted: dict | None = None) -> SimpleNamespace:
    shipment_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(), shipment_id=shipment_id, kind=kind,
        number=number, doc_date=None, extracted=extracted or {},
    )


async def test_wrong_kind_rejected():
    row = _doc_row(kind="invoice")
    with pytest.raises(PermitKindError):
        await verify_permit(FakeSession(row), row.shipment_id, row.id)


async def test_format_invalid_persisted():
    row = _doc_row(number=None)
    session = FakeSession(row)
    result, _ = await verify_permit(session, row.shipment_id, row.id)
    assert result.status == "format_invalid"
    assert result.source == "format"
    assert session.flushed
    assert row.extracted["permit_check"]["status"] == "format_invalid"


async def test_registry_disabled_format_ok(monkeypatch):
    monkeypatch.setattr(settings, "TULPAR_SEARCH_URL", "")
    row = _doc_row()
    result, _ = await verify_permit(FakeSession(row), row.shipment_id, row.id)
    assert result.status == "format_ok"
    assert "TULPAR_SEARCH_URL" in result.detail
    assert row.extracted["permit_check"]["status"] == "format_ok"


async def test_registry_called_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "TULPAR_SEARCH_URL", "https://example.test/search")
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    async def fake_search(number):
        return PermitCheckResult(
            status="found", detail="номер найден в реестре",
            source="registry", checked_at=now,
        )

    monkeypatch.setattr(tulpar, "search_registry", fake_search)
    row = _doc_row()
    result, _ = await verify_permit(FakeSession(row), row.shipment_id, row.id)
    assert result.status == "found"
    assert result.source == "registry"
    assert row.extracted["permit_check"]["status"] == "found"


async def test_fresh_cache_returned_without_recheck(monkeypatch):
    called = False

    async def fake_search(number):
        nonlocal called
        called = True
        raise AssertionError("не должен вызываться при свежем кэше")

    monkeypatch.setattr(tulpar, "search_registry", fake_search)
    fresh_at = dt.datetime.now(dt.timezone.utc).isoformat()
    row = _doc_row(extracted={"permit_check": {
        "status": "found", "detail": "из кэша", "source": "registry",
        "checked_at": fresh_at,
    }})
    session = FakeSession(row)

    result, _ = await verify_permit(session, row.shipment_id, row.id)
    assert result.cached is True
    assert result.status == "found"
    assert called is False
    assert session.flushed is False


async def test_stale_cache_rechecked(monkeypatch):
    monkeypatch.setattr(settings, "TULPAR_SEARCH_URL", "")
    stale_at = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)
    ).isoformat()
    row = _doc_row(extracted={"permit_check": {
        "status": "found", "detail": "старый", "source": "registry",
        "checked_at": stale_at,
    }})
    result, _ = await verify_permit(FakeSession(row), row.shipment_id, row.id)
    assert result.cached is False
    assert result.status == "format_ok"    # перепроверен (реестр отключён)


async def test_force_ignores_cache(monkeypatch):
    monkeypatch.setattr(settings, "TULPAR_SEARCH_URL", "")
    fresh_at = dt.datetime.now(dt.timezone.utc).isoformat()
    row = _doc_row(extracted={"permit_check": {
        "status": "not_found", "detail": "кэш", "source": "registry",
        "checked_at": fresh_at,
    }})
    result, _ = await verify_permit(
        FakeSession(row), row.shipment_id, row.id, force=True
    )
    assert result.cached is False
    assert result.status == "format_ok"
