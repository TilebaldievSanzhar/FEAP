"""classify_link: нормализация кода, отбор кандидатов, обратная запись."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from feap.dt.classify_link import (
    apply_item_result,
    build_batch_items,
    normalize_code,
    split_candidates,
)


class TestNormalizeCode:
    @pytest.mark.parametrize("raw,expected", [
        ("8509 80 000 0", "8509800000"),
        ("8509800000", "8509800000"),
        ("6403.99.00.00", "6403990000"),
        ("850980", None),          # меньше 10 цифр
        ("85098000001", None),     # больше 10 цифр
        ("", None),
        (None, None),
    ])
    def test_cases(self, raw, expected):
        assert normalize_code(raw) == expected


def _row(position: int, hs_code: str | None, **data) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        position=position,
        hs_code=hs_code,
        data={"description": f"Товар {position}", **data},
    )


class TestSplitCandidates:
    def test_only_uncoded(self):
        rows = [_row(1, "8509800000"), _row(2, None), _row(3, None)]
        candidates, skipped = split_candidates(rows, force=False)
        assert [r.position for r in candidates] == [2, 3]
        assert skipped == 1

    def test_force_takes_all(self):
        rows = [_row(1, "8509800000"), _row(2, None)]
        candidates, skipped = split_candidates(rows, force=True)
        assert len(candidates) == 2
        assert skipped == 0


class TestBuildBatchItems:
    def test_source_ref_and_additional_info(self):
        shipment_id = uuid.uuid4()
        rows = [_row(1, None, origin_country="CN"), _row(2, None)]
        items = build_batch_items(rows, shipment_id=shipment_id, force=False)

        assert items[0].description == "Товар 1"
        assert items[0].additional_info == {"страна происхождения": "CN"}
        assert items[1].additional_info == {}
        ref = items[0].source_ref
        assert ref["shipment_id"] == str(shipment_id)
        assert ref["shipment_item_id"] == str(rows[0].id)
        assert ref["position"] == 1
        assert ref["overwrite"] is False

    def test_force_sets_overwrite(self):
        items = build_batch_items(
            [_row(1, "8509800000")], shipment_id=uuid.uuid4(), force=True
        )
        assert items[0].source_ref["overwrite"] is True


# ── Обратная запись ──────────────────────────────────────────────────────────

class FakeDb:
    """AsyncSession-минимум для apply_item_result: get + commit."""

    def __init__(self, row):
        self._row = row
        self.committed = False

    async def get(self, _model, _pk):
        return self._row

    async def commit(self):
        self.committed = True


RESULT = {
    "final_code": "8509 80 000 0",
    "code_description": "Машины электромеханические бытовые...",
    "confidence": 0.87,
}


def _shipment_row(hs_code: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        hs_code=hs_code,
        data={"description": "Зубная щётка электрическая", "hs_code": hs_code},
    )


async def test_writes_code_into_empty_item():
    row = _shipment_row()
    db = FakeDb(row)
    job_item_id = uuid.uuid4()

    ok = await apply_item_result(
        db, job_item_id=job_item_id,
        source_ref={"shipment_item_id": str(row.id), "overwrite": False},
        result_json=RESULT,
    )

    assert ok is True and db.committed
    assert row.hs_code == "8509800000"
    assert row.data["hs_code"] == "8509800000"
    assert row.data["classification_ref"] == f"job_item:{job_item_id}"
    assert row.data["extra"]["classification"]["confidence"] == 0.87


async def test_manual_code_not_clobbered():
    row = _shipment_row(hs_code="6403990000")
    db = FakeDb(row)

    ok = await apply_item_result(
        db, job_item_id=uuid.uuid4(),
        source_ref={"shipment_item_id": str(row.id), "overwrite": False},
        result_json=RESULT,
    )

    assert ok is False
    assert row.hs_code == "6403990000"
    assert not db.committed


async def test_force_overwrites():
    row = _shipment_row(hs_code="6403990000")
    db = FakeDb(row)

    ok = await apply_item_result(
        db, job_item_id=uuid.uuid4(),
        source_ref={"shipment_item_id": str(row.id), "overwrite": True},
        result_json=RESULT,
    )

    assert ok is True
    assert row.hs_code == "8509800000"


async def test_bad_final_code_skips():
    row = _shipment_row()
    db = FakeDb(row)

    ok = await apply_item_result(
        db, job_item_id=uuid.uuid4(),
        source_ref={"shipment_item_id": str(row.id)},
        result_json={"final_code": "85 09"},
    )

    assert ok is False
    assert row.hs_code is None


async def test_vanished_row_returns_false():
    db = FakeDb(None)
    ok = await apply_item_result(
        db, job_item_id=uuid.uuid4(),
        source_ref={"shipment_item_id": str(uuid.uuid4())},
        result_json=RESULT,
    )
    assert ok is False
