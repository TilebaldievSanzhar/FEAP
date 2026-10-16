"""Сверка реестра мер ЕЭК: что считается изменением и что ждёт человека."""

from __future__ import annotations

import datetime as dt

from feap.refsync.remedies import diff_measures

AD32_DB = {
    "measure_no": "AD-32", "kind": "antidumping", "title": "Алюминиевая посуда",
    "product_description": "Посуда литая из алюминия", "countries": ["CN"],
    "codes": ["7615101000", "7615108009"],
    "rates": [{"rate_pct": "21.89", "country": None, "producers": [], "variant": None}],
    "status": "applied", "valid_from": dt.date(2021, 9, 25), "valid_to": dt.date(2027, 5, 28),
    "needs_review": False,
}
AD32_REGISTRY = {
    **AD32_DB,
    "codes": ["7615108009", "7615101000"],                    # другой порядок
    "rates": [{"rate_pct": "21.890", "country": None, "producers": [], "variant": None}],
    "valid_from": "2021-09-25", "valid_to": "2027-05-28",     # строки вместо дат
    "product_description": "Посуда  литая из алюминия",        # лишний пробел
    "rate_text": "21,89%", "needs_review": False,
}


def test_same_measure_in_other_format_is_not_a_change():
    assert diff_measures({"AD-32": AD32_DB}, {"AD-32": AD32_REGISTRY}) == []


def test_rate_change_needs_review_and_keeps_text_for_reviewer():
    new = {**AD32_REGISTRY, "rates": [{"rate_pct": "25.1", "country": None, "producers": []}],
           "rate_text": "25,1% (пересмотр)", "needs_review": False}
    [change] = diff_measures({"AD-32": AD32_DB}, {"AD-32": new})
    assert change.change == "updated" and change.fields == ["rates"]
    assert change.needs_review
    assert change.after["rate_text"] == "25,1% (пересмотр)"
    assert change.after["parsed_needs_review"] is False
    assert change.before["rates"][0]["rate_pct"] == "21.89"


def test_structural_change_applies_without_review():
    new = {**AD32_REGISTRY, "valid_to": "2032-05-28", "status": "applied"}
    [change] = diff_measures({"AD-32": AD32_DB}, {"AD-32": new})
    assert change.fields == ["valid_to"] and not change.needs_review
    assert change.before == {"valid_to": "2027-05-28"} and change.after == {"valid_to": "2032-05-28"}


def test_expiry_is_a_structural_update():
    new = {**AD32_REGISTRY, "status": "expired"}
    [change] = diff_measures({"AD-32": AD32_DB}, {"AD-32": new})
    assert change.fields == ["status"] and not change.needs_review


def test_new_measure_added_for_review_historical_skipped():
    fresh = {**AD32_REGISTRY, "measure_no": "AD-42", "title": "Новая мера",
             "valid_from": "2026-11-01", "needs_review": True, "rate_text": "?"}
    historical = {**AD32_REGISTRY, "measure_no": "AD-5", "status": "expired"}
    changes = diff_measures({}, {"AD-42": fresh, "AD-5": historical})
    assert [c.measure_no for c in changes] == ["AD-42"]
    [added] = changes
    assert added.change == "added" and added.needs_review
    assert added.effective_from == dt.date(2026, 11, 1)
    assert added.after["parsed_needs_review"] is True


def test_measure_missing_from_registry_is_reported_not_removed():
    expired = {**AD32_DB, "measure_no": "AD-1", "status": "expired"}
    changes = diff_measures({"AD-32": AD32_DB, "AD-1": expired}, {})
    assert [(c.measure_no, c.change) for c in changes] == [("AD-32", "missing")]
    assert changes[0].before == {"status": "applied", "title": "Алюминиевая посуда"}
