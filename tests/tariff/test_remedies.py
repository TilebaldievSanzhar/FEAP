"""Меры защиты рынка ЕАЭС: разбор текста реестра ЕЭК, сопоставление, выбор ставки."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from feap.tariff.remedies import (
    RemedyMeasure,
    RemedyRate,
    find_code_measures,
    find_measures,
    measure_from_row,
    normalize_company,
    origin_matches,
    parse_rate_lines,
    producer_matches,
    record_to_seed,
    select_rate,
    split_producers,
)

SEED = Path(__file__).resolve().parents[2] / "data" / "trade_remedies_seed.json"
DAY = dt.date(2026, 10, 13)


# ── Разбор текста «формы меры» (фрагменты реальных записей реестра ЕЭК) ────

def test_single_rate():
    parsed = parse_rate_lines(["Антидемпинговая пошлина в размере 21,89 % от таможенной стоимости"])
    assert [r.rate_pct for r in parsed.rates] == [Decimal("21.89")]
    assert parsed.rates[0].producers == ()
    assert not parsed.needs_review


def test_producer_rates_and_others():
    parsed = parse_rate_lines([
        "Антидемпинговая пошлина в процентах от таможенной стоимости:",
        "- для Xuanhua Construction Machinery Co., Ltd. — 9,65 %;",
        "- для Shantui Construction Machinery Co., Ltd. — 11,31 %;",
        "- для прочих производителей — 44,65 %.",
    ])
    assert [(r.producers, r.rate_pct) for r in parsed.rates] == [
        (("Xuanhua Construction Machinery Co., Ltd.",), Decimal("9.65")),
        (("Shantui Construction Machinery Co., Ltd.",), Decimal("11.31")),
        ((), Decimal("44.65")),
    ]


def test_country_sections_and_undertakings():
    parsed = parse_rate_lines([
        "Антидемпинговая мера в следующих формах:",
        "А. Антидемпинговая пошлина для оцинкованного проката, происходящего из "
        "Китайской Народной Республики, в процентах от таможенной стоимости:",
        "– для Angang Steel Company Limited — 17,00 %;",
        "– для прочих производителей — 17,00 %.",
        "Б. Антидемпинговая пошлина для оцинкованного проката, происходящего из Украины, "
        "в процентах от таможенной стоимости:",
        "– для прочих производителей — 23,90 %.",
        "В. Ценовые обязательства компаний:",
        "- Angang Steel Company Limited;",
    ])
    assert [(r.country, r.producers, r.rate_pct) for r in parsed.rates] == [
        ("CN", ("Angang Steel Company Limited",), Decimal("17.00")),
        ("CN", (), Decimal("17.00")),
        ("UA", (), Decimal("23.90")),
    ]
    assert parsed.undertakings == ["Angang Steel Company Limited"]


def test_country_in_line():
    parsed = parse_rate_lines([
        "Антидемпинговая пошлина в процентах от таможенной стоимости:",
        "- для алюминиевой ленты, происходящей из Азербайджанской Республики — 16,18 %;",
        "- для алюминиевой ленты, происходящей из Китайской Народной Республики — 13,14 %.",
    ])
    assert [(r.country, r.rate_pct) for r in parsed.rates] == [
        ("AZ", Decimal("16.18")), ("CN", Decimal("13.14")),
    ]


def test_variants_and_footnote():
    parsed = parse_rate_lines([
        "Антидемпинговые пошлины:",
        "а) для обсадных труб в размере 18,9 % от таможенной стоимости;",
        "в) для нефтепроводных труб в следующих процентах от таможенной стоимости:",
        "- для ОАО «ИНТЕРПАЙП НТЗ» — 19,4 %;",
        "- для прочих производителей — 37,8 %.",
        "* Антидемпинговая пошлина не применяется в отношении бывших в употреблении дисков.",
    ])
    assert [(r.variant, r.producers, r.rate_pct) for r in parsed.rates] == [
        ("обсадных труб", (), Decimal("18.9")),
        ("нефтепроводных труб", ("ОАО «ИНТЕРПАЙП НТЗ»",), Decimal("19.4")),
        ("нефтепроводных труб", (), Decimal("37.8")),
    ]
    assert parsed.footnotes and "бывших в употреблении" in parsed.footnotes[0]


def test_rate_range_needs_review():
    parsed = parse_rate_lines([
        "Антидемпинговая пошлина в размерах от 27,47 % до 52,23 % от таможенной стоимости.",
    ])
    assert parsed.needs_review
    assert parsed.rates == []


def test_split_producers_keeps_legal_suffixes():
    assert split_producers(
        "Hengyang Valin Steel Tube Co., Ltd., Hengyang Valin MPM Co., Ltd."
    ) == ["Hengyang Valin Steel Tube Co., Ltd.", "Hengyang Valin MPM Co., Ltd."]
    assert split_producers(
        "Shandong Sino Steel Co., Ltd. и Dalian Posco Co., Ltd."
    ) == ["Shandong Sino Steel Co., Ltd.", "Dalian Posco Co., Ltd."]
    assert split_producers("компаний A Corp; B Ltd") == ["A Corp", "B Ltd"]


# ── Запись реестра → сид ────────────────────────────────────────────────────

def _record(**over) -> dict:
    base = {
        "_id": {"$oid": "abc"},
        "investigationnumber": "AD-32",
        "investigationtype": "AD",
        "shortname": "Алюминиевая посуда",
        "description": "<p>Штампованная и литая алюминиевая посуда</p>",
        "exportingcountrycode": ["CN"],
        "tnved": ["7615 10 100 0", "7616 99 900 8", "7615 10 100 0"],
        "actiondescription": "<p>Антидемпинговая пошлина в размере 21,89 % от таможенной стоимости</p>",
        "foundation": "<p>Решение Коллегии ЕЭК от 24 августа 2021 г. № 107</p>",
        "note": "",
        "statuscode": "1",
        "actual": True,
        "startdate": "2021-09-25T00:00:00Z",
        "enddate": "2027-05-28T23:59:59Z",
        "modified": "2026-09-01T00:00:00Z",
    }
    base.update(over)
    return base


def test_record_to_seed_maps_fields():
    seed = record_to_seed(_record(), today=DAY)
    assert seed["measure_no"] == "AD-32"
    assert seed["kind"] == "antidumping"
    assert seed["codes"] == ["7615101000", "7616999008"]  # цифры, без дублей
    assert seed["rates"] == [
        {"rate_pct": "21.89", "country": None, "producers": [], "variant": None},
    ]
    assert seed["status"] == "applied"
    assert seed["valid_to"] == "2027-05-28"
    assert seed["source_url"].endswith("/card/abc")


def test_record_status_mapping():
    assert record_to_seed(_record(statuscode="2", actual=True), today=DAY)["status"] == "suspended"
    assert record_to_seed(_record(statuscode="2", actual=False), today=DAY)["status"] == "expired"
    assert record_to_seed(
        _record(enddate="2024-01-01T00:00:00Z"), today=DAY,
    )["status"] == "expired"


# ── Сопоставление и выбор ставки ────────────────────────────────────────────

def _measure(**over) -> RemedyMeasure:
    base = dict(
        measure_no="AD-17", kind="antidumping", title="Гусеничные бульдозеры",
        countries=("CN",), codes=("8429110090",), status="applied",
        valid_from=dt.date(2015, 12, 12), valid_to=dt.date(2027, 2, 25),
        rates=(
            RemedyRate(Decimal("9.65"), producers=("Xuanhua Construction Machinery Co., Ltd.",)),
            RemedyRate(Decimal("11.31"), producers=("Shantui Construction Machinery Co., Ltd.",)),
            RemedyRate(Decimal("44.65")),
        ),
    )
    base.update(over)
    return RemedyMeasure(**base)


def test_find_measures_code_origin_date():
    m = _measure()
    assert find_measures([m], code10="8429110090", origin="CN", on_date=DAY) == [m]
    assert find_measures([m], code10="8429110090", origin="TR", on_date=DAY) == []
    assert find_measures([m], code10="8429110090", origin="CN",
                         on_date=dt.date(2027, 3, 1)) == []
    assert find_measures([_measure(status="suspended")], code10="8429110090",
                         origin="CN", on_date=DAY) == []


def test_heading_level_codes_match_by_prefix():
    m = _measure(codes=("7210", "7212"))
    assert find_code_measures([m], code10="7210490001", on_date=DAY) == [m]
    assert find_code_measures([m], code10="7211130000", on_date=DAY) == []


def test_origin_matches_eu_and_all():
    assert origin_matches(("EU",), "DE")
    assert origin_matches(("ALL",), "VN")
    assert not origin_matches(("CN",), None)


def test_select_rate_by_manufacturer():
    choice = select_rate(_measure(), origin="CN",
                         manufacturer="SHANTUI CONSTRUCTION MACHINERY CO LTD")
    assert choice.rate.rate_pct == Decimal("11.31")
    assert "индивидуальная" in choice.note


def test_select_rate_others_when_manufacturer_unknown_or_missing():
    unknown = select_rate(_measure(), origin="CN", manufacturer="Some Other Factory Ltd")
    assert unknown.rate.rate_pct == Decimal("44.65")
    assert unknown.note == "ставка для прочих производителей"
    missing = select_rate(_measure(), origin="CN")
    assert missing.rate.rate_pct == Decimal("44.65")
    assert "не указан" in missing.note


def test_select_rate_country_specific():
    m = _measure(countries=("AZ", "CN"), rates=(
        RemedyRate(Decimal("16.18"), country="AZ"), RemedyRate(Decimal("13.14"), country="CN"),
    ))
    assert select_rate(m, origin="AZ").rate.rate_pct == Decimal("16.18")
    assert select_rate(m, origin="CN").rate.rate_pct == Decimal("13.14")


def test_select_rate_variants():
    m = _measure(rates=(
        RemedyRate(Decimal("18.9"), variant="обсадных труб"),
        RemedyRate(Decimal("37.8"), variant="нефтепроводных труб"),
    ))
    assert select_rate(m, origin="CN").rate is None
    assert "вид товара" in select_rate(m, origin="CN").missing[0]
    assert select_rate(m, origin="CN", variant="нефтепроводных").rate.rate_pct == Decimal("37.8")


def test_select_rate_only_named_producers_requires_manufacturer():
    m = _measure(rates=(RemedyRate(Decimal("10"), producers=("Acme Steel Co., Ltd.",)),))
    choice = select_rate(m, origin="CN", manufacturer="Unknown Works")
    assert choice.rate is None and choice.missing


def test_company_normalization():
    assert normalize_company("ООО «Подшипники качения Уси»") == "подшипники качения уси"
    assert producer_matches("Tianjin Pipe Corporation", "tianjin pipe corporation")
    assert not producer_matches("Acme", "Acme Steel Group")   # слишком короткое имя


# ── Снимок реестра в репозитории ────────────────────────────────────────────

def test_seed_snapshot_is_consistent():
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    measures = [measure_from_row(m) for m in seed["measures"]]
    assert measures, "снимок мер пуст"
    for m in measures:
        assert m.codes and all(c.isdigit() for c in m.codes), m.measure_no
        assert m.status in {"applied", "suspended"}, m.measure_no
        if m.status == "applied" and not m.needs_review:
            assert m.rates, f"{m.measure_no}: нет ставок"
    cookware = next(m for m in measures if m.measure_no == "AD-32")
    assert cookware.rates[0].rate_pct == Decimal("21.89")
    assert "7615101000" in cookware.codes
