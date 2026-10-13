"""Тарифные меры в проекте ДТ: сбор 0,4%, антидемпинг, ЗСТ, условный НДС + ФЛК."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from feap.dt.assembler import DocEntry, ItemRef, Rates, VatOption, assemble
from feap.dt.flk import CodeInfo, draft_status, validate_draft
from feap.dt.schemas import PartyIn, RemedyDecision, ShipmentIn, ShipmentItemIn
from feap.tariff.agreements import TradeAgreementRef
from feap.tariff.fees import kg_release_fee_rule
from feap.tariff.remedies import RemedyMeasure, RemedyRate

RATES = Rates(rate_date=dt.date(2026, 10, 13), contract=Decimal("87.5"),
              usd=Decimal("87.5"), eur=Decimal("95"))

COOKWARE = RemedyMeasure(
    measure_no="AD-32", kind="antidumping", title="Алюминиевая посуда",
    countries=("CN",), codes=("7615101000", "7615108009"), status="applied",
    valid_from=dt.date(2021, 9, 25), valid_to=dt.date(2027, 5, 28),
    rates=(RemedyRate(Decimal("21.89")),),
)
BULLDOZERS = RemedyMeasure(
    measure_no="AD-17", kind="antidumping", title="Гусеничные бульдозеры",
    countries=("CN",), codes=("8429110090",), status="applied",
    valid_from=dt.date(2015, 12, 12), valid_to=dt.date(2027, 2, 25),
    rates=(
        RemedyRate(Decimal("11.31"), producers=("Shantui Construction Machinery Co., Ltd.",)),
        RemedyRate(Decimal("44.65")),
    ),
)
VIETNAM = TradeAgreementRef(
    country="VN", regime="fta", agreement="Соглашение о ЗСТ ЕАЭС–Вьетнам",
    status="in_force", in_force_from=dt.date(2016, 10, 5),
)

COOK_REF = ItemRef(code_exists=True, is_leaf=True, duty_raw="10%", vat_rate="12%")
CTX_CODES = {
    "7615101000": CodeInfo(exists=True, is_leaf=True),
    "8429110090": CodeInfo(exists=True, is_leaf=True),
    "1001990000": CodeInfo(exists=True, is_leaf=True),
}


class FakeCtx:
    def code_info(self, code10: str) -> CodeInfo | None:
        return CTX_CODES.get(code10)

    def classifier_codes(self, classifier: str) -> set[str] | None:
        return None

    def has_ntm_measures(self, code10: str) -> bool:
        return False


def shipment(item: ShipmentItemIn, **over) -> ShipmentIn:
    base = dict(
        seller=PartyIn(name="Yongkang Cookware Co., Ltd", country="CN"),
        buyer=PartyIn(name="ОсОО «Посуда»", country="KG", inn="01234567890123"),
        incoterms="DAP", contract_currency="USD", items=[item],
    )
    base.update(over)
    return ShipmentIn(**base)


def cookware_item(**over) -> ShipmentItemIn:
    base = dict(
        description="Кастрюли алюминиевые литые с антипригарным покрытием, бытовые",
        hs_code="7615101000", origin_country="CN", price=Decimal("10000"),
        net_kg=Decimal("900"), gross_kg=Decimal("1000"),
    )
    base.update(over)
    return ShipmentItemIn(**base)


def lines(result, kind: str) -> list[dict]:
    return [p for p in result.graphs["items"][0]["g47"] if p["kind"] == kind]


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


# ── Антидемпинговые меры ────────────────────────────────────────────────────

class TestRemedies:
    def test_undecided_measure_stops_draft(self):
        ship = shipment(cookware_item())
        result = assemble(ship, [COOK_REF], RATES, remedies=[COOKWARE])
        assert any("AD-32" in m for m in result.missing_parameters)
        assert lines(result, "antidumping") == []
        findings = validate_draft(result.graphs, ship, FakeCtx())
        assert draft_status(findings, result.missing_parameters) == "needs_input"

    def test_applies_adds_line_2050_and_excludes_from_vat_base(self):
        item = cookware_item(remedy_decisions={"AD-32": RemedyDecision(decision="applies")})
        ship = shipment(item)
        result = assemble(ship, [COOK_REF], RATES, remedies=[COOKWARE])
        assert result.missing_parameters == []
        cv = Decimal(result.graphs["items"][0]["g45"])          # 875 000
        ad = lines(result, "antidumping")[0]
        assert ad["code"] == "2050" and ad["ref"] == "AD-32"
        assert Decimal(ad["amount"]) == (cv * Decimal("0.2189")).quantize(Decimal("0.01"))
        vat = lines(result, "vat")[0]
        assert Decimal(vat["basis"]) == cv + Decimal(lines(result, "duty")[0]["amount"])
        assert result.graphs["totals"]["payments"]["antidumping"] == ad["amount"]
        findings = validate_draft(result.graphs, ship, FakeCtx())
        assert not [f for f in findings if f.severity == "error"], findings
        assert "W-REMEDY-VAT" in rules(findings)

    def test_vat_base_can_include_remedy_duties(self):
        item = cookware_item(remedy_decisions={"AD-32": RemedyDecision(decision="applies")})
        ship = shipment(item)
        result = assemble(ship, [COOK_REF], RATES, remedies=[COOKWARE],
                          vat_includes_remedies=True)
        cv = Decimal(result.graphs["items"][0]["g45"])
        expected = (cv + Decimal(lines(result, "duty")[0]["amount"])
                    + Decimal(lines(result, "antidumping")[0]["amount"]))
        assert Decimal(lines(result, "vat")[0]["basis"]) == expected
        findings = validate_draft(result.graphs, ship, FakeCtx())
        assert not [f for f in findings if f.rule == "F-10"], findings

    def test_not_applicable_is_recorded(self):
        item = cookware_item(remedy_decisions={
            "AD-32": RemedyDecision(decision="not_applicable", note="штампованная сталь"),
        })
        ship = shipment(item)
        result = assemble(ship, [COOK_REF], RATES, remedies=[COOKWARE])
        assert lines(result, "antidumping") == [] and result.missing_parameters == []
        assert "I-REMEDY-NA" in rules(validate_draft(result.graphs, ship, FakeCtx()))

    def test_exempt_requires_document(self):
        item = cookware_item(remedy_decisions={"AD-32": RemedyDecision(decision="exempt")})
        ship = shipment(item)
        bare = assemble(ship, [COOK_REF], RATES, remedies=[COOKWARE])
        assert "W-REMEDY-EXEMPT" in rules(validate_draft(bare.graphs, ship, FakeCtx()))
        with_doc = assemble(ship, [COOK_REF], RATES, remedies=[COOKWARE],
                            documents=[DocEntry(kind="permit", number="12/2026")])
        assert "W-REMEDY-EXEMPT" not in rules(validate_draft(with_doc.graphs, ship, FakeCtx()))

    def test_other_origin_is_not_matched(self):
        ship = shipment(cookware_item(origin_country="TR"))
        result = assemble(ship, [COOK_REF], RATES, remedies=[COOKWARE])
        assert result.missing_parameters == []
        assert result.graphs["items"][0]["tariff"]["remedies"] == []

    def test_individual_producer_rate(self):
        item = ShipmentItemIn(
            description="Бульдозер гусеничный с неповоротным отвалом, 160 л.с., новый",
            hs_code="8429110090", origin_country="CN", price=Decimal("50000"),
            manufacturer="Shantui Construction Machinery Co., Ltd.",
            remedy_decisions={"AD-17": RemedyDecision(decision="applies")},
        )
        result = assemble(shipment(item), [COOK_REF], RATES, remedies=[BULLDOZERS])
        assert lines(result, "antidumping")[0]["rate"] == "11.31%"

    def test_manual_rate_override(self):
        item = cookware_item(remedy_decisions={
            "AD-32": RemedyDecision(decision="applies", rate_pct=Decimal("5")),
        })
        result = assemble(shipment(item), [COOK_REF], RATES, remedies=[COOKWARE])
        line = lines(result, "antidumping")[0]
        assert line["rate"] == "5%" and "вручную" in line["note"]


# ── ЗСТ и страна происхождения ──────────────────────────────────────────────

class TestOrigin:
    def test_fta_preferential_rate_applied(self):
        item = cookware_item(origin_country="VN", preference="ОТОО",
                             preferential_duty_rate="0%")
        ship = shipment(item)
        result = assemble(ship, [COOK_REF], RATES, agreements=[VIETNAM],
                          documents=[DocEntry(kind="cert_origin", number="EAV-1")])
        duty = lines(result, "duty")[0]
        assert Decimal(duty["amount"]) == Decimal("0.00")
        assert "преференциальная ставка" in duty["note"]
        findings = validate_draft(result.graphs, ship, FakeCtx())
        assert "F-16" not in rules(findings) and "F-13" not in rules(findings)

    def test_fta_rate_without_preference_code_is_rejected(self):
        item = cookware_item(origin_country="VN", preferential_duty_rate="0%")
        ship = shipment(item)
        result = assemble(ship, [COOK_REF], RATES, agreements=[VIETNAM])
        assert Decimal(lines(result, "duty")[0]["amount"]) > 0   # ставка ЕТТ
        f16 = [f for f in validate_draft(result.graphs, ship, FakeCtx()) if f.rule == "F-16"]
        assert f16 and "графе 36" in f16[0].message

    def test_fta_rate_for_country_without_agreement_is_rejected(self):
        item = cookware_item(preference="ОТОО", preferential_duty_rate="0%")   # CN
        ship = shipment(item)
        result = assemble(ship, [COOK_REF], RATES, agreements=[VIETNAM])
        f16 = [f for f in validate_draft(result.graphs, ship, FakeCtx()) if f.rule == "F-16"]
        assert f16 and "нет действующего" in f16[0].message

    def test_fta_hint_and_eaeu_info(self):
        vn = shipment(cookware_item(origin_country="VN"))
        res = assemble(vn, [COOK_REF], RATES, agreements=[VIETNAM])
        assert "W-FTA" in rules(validate_draft(res.graphs, vn, FakeCtx()))
        kz = shipment(cookware_item(origin_country="KZ"))
        res = assemble(kz, [COOK_REF], RATES, agreements=[VIETNAM])
        assert "I-EAEU" in rules(validate_draft(res.graphs, kz, FakeCtx()))


# ── Условный НДС (ПКМ № 816 и др.) ──────────────────────────────────────────

WHEAT_REF = ItemRef(
    code_exists=True, is_leaf=True, duty_raw="5%", vat_rate="12%",
    vat_options=(VatOption(
        source="pkm816_a2", rate="0%", legal_ref="ПКМ КР № 816, прил. 2",
        condition="для производства кормов",
    ),),
)


def wheat(**over) -> ShipmentItemIn:
    base = dict(description="Пшеница мягкая прочая, несеменная, урожай 2026",
                hs_code="1001990000", origin_country="KZ", price=Decimal("20000"))
    base.update(over)
    return ShipmentItemIn(**base)


class TestConditionalVat:
    def test_default_rate_with_warning(self):
        ship = shipment(wheat())
        res = assemble(ship, [WHEAT_REF], RATES)
        assert lines(res, "vat")[0]["rate"] == "12%"
        assert "W-VAT-COND" in rules(validate_draft(res.graphs, ship, FakeCtx()))

    def test_chosen_benefit_applies_zero_rate(self):
        ship = shipment(wheat(vat_benefit="pkm816_a2"))
        res = assemble(ship, [WHEAT_REF], RATES)
        vat = lines(res, "vat")[0]
        assert vat["rate"] == "0%" and Decimal(vat["amount"]) == 0
        assert vat["ref"] == "pkm816_a2"
        found = rules(validate_draft(res.graphs, ship, FakeCtx()))
        assert "I-VAT-COND" in found and "W-VAT-COND" not in found

    def test_unknown_benefit_is_error(self):
        ship = shipment(wheat(vat_benefit="pkm249"))
        res = assemble(ship, [WHEAT_REF], RATES)
        assert lines(res, "vat")[0]["rate"] == "12%"
        assert "F-17" in rules(validate_draft(res.graphs, ship, FakeCtx()))


# ── Сбор за таможенные операции ─────────────────────────────────────────────

class TestFee:
    def test_percent_fee_from_total_customs_value(self):
        res = assemble(shipment(cookware_item()), [COOK_REF], RATES,
                       fee_rule=kg_release_fee_rule())
        cv = Decimal(res.graphs["header"]["g12"])                # 875 000
        assert Decimal(res.graphs["totals"]["payments"]["fee"]) == cv * Decimal("0.004")
        assert res.graphs["totals"]["fee_line"]["code"] == "1010"
        assert res.graphs["meta"]["fee_mode"] == "percent"

    def test_minimum_fee(self):
        small = cookware_item(price=Decimal("10"))
        res = assemble(shipment(small), [COOK_REF], RATES, fee_rule=kg_release_fee_rule())
        assert res.graphs["totals"]["payments"]["fee"] == "500.00"
        assert "минимальный" in res.graphs["totals"]["fee_line"]["note"]

    def test_fixed_override_wins(self):
        res = assemble(shipment(cookware_item()), [COOK_REF], RATES,
                       fee_kgs=Decimal("2000"), fee_rule=kg_release_fee_rule())
        assert res.graphs["totals"]["payments"]["fee"] == "2000.00"

    def test_no_fee_rule_gives_info(self):
        ship = shipment(cookware_item())
        res = assemble(ship, [COOK_REF], RATES)
        assert "W-FEE" in rules(validate_draft(res.graphs, ship, FakeCtx()))
