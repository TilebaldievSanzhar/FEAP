"""catalog: чистая логика автоподстановки кодов по SKU."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

from feap.dt.catalog import substitute_codes
from feap.dt.schemas import ShipmentItemIn


def _item(sku: str | None = None, hs_code: str | None = None) -> ShipmentItemIn:
    return ShipmentItemIn(
        description="Щётка зубная электрическая бытовая",
        sku=sku,
        hs_code=hs_code,
        price=Decimal("100"),
    )


def _entry(hs_code: str | None = "8509800000") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), hs_code=hs_code)


def test_fills_empty_code_and_sets_ref():
    entry = _entry()
    items = [_item(sku="TB-01")]

    used = substitute_codes(items, {"TB-01": entry})

    assert items[0].hs_code == "8509800000"
    assert items[0].classification_ref == f"catalog:{entry.id}"
    assert used == [entry.id]


def test_existing_code_untouched():
    items = [_item(sku="TB-01", hs_code="6403990000")]

    used = substitute_codes(items, {"TB-01": _entry()})

    assert items[0].hs_code == "6403990000"
    assert items[0].classification_ref is None
    assert used == []


def test_unknown_sku_and_no_sku_skipped():
    items = [_item(sku="NOPE"), _item(sku=None)]
    used = substitute_codes(items, {"TB-01": _entry()})
    assert all(i.hs_code is None for i in items)
    assert used == []


def test_entry_without_code_skipped():
    items = [_item(sku="TB-01")]
    used = substitute_codes(items, {"TB-01": _entry(hs_code=None)})
    assert items[0].hs_code is None
    assert used == []


def test_duplicate_sku_fills_both_items():
    entry = _entry()
    items = [_item(sku="TB-01"), _item(sku="TB-01")]

    used = substitute_codes(items, {"TB-01": entry})

    assert [i.hs_code for i in items] == ["8509800000", "8509800000"]
    assert used == [entry.id, entry.id]
