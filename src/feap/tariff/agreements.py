"""Торговые режимы по стране происхождения: ЗСТ ЕАЭС, временные соглашения, члены ЕАЭС.

Модуль не знает графиков снижения ставок (они по тысячам строк на каждое
соглашение) — он говорит, ЕСТЬ ли у страны действующий преференциальный
режим. Преференциальная ставка вводится декларантом по графику соглашения
и применяется только при действующем режиме, коде преференции в графе 36
и сертификате происхождения в графе 44 (ФЛК F-13/F-16).

Единая система тарифных преференций ЕАЭС (развивающиеся/наименее развитые
страны) здесь не моделируется: Китай исключён из неё с 12.10.2021, а
перечни стран и товаров — отдельный набор данных (TODO).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

EAEU_MEMBERS = frozenset({"AM", "BY", "KZ", "KG", "RU"})

#: Режимы, при которых возможна преференциальная ставка ввозной пошлины.
PREFERENTIAL_REGIMES = frozenset({"fta", "fta_interim"})


@dataclass(frozen=True)
class TradeAgreementRef:
    country: str
    regime: str                  # eaeu_member | fta | fta_interim
    agreement: str
    status: str                  # in_force | signed | terminated
    in_force_from: dt.date | None = None
    in_force_to: dt.date | None = None
    origin_proof: str | None = None
    legal_ref: str | None = None
    note: str | None = None
    verified: bool = False


def is_in_force(agreement: TradeAgreementRef, on_date: dt.date) -> bool:
    if agreement.status != "in_force":
        return False
    if agreement.in_force_from and agreement.in_force_from > on_date:
        return False
    return not (agreement.in_force_to and agreement.in_force_to < on_date)


def regime_for_origin(
    agreements: list[TradeAgreementRef],
    country: str | None,
    on_date: dt.date,
) -> TradeAgreementRef | None:
    """Действующий на дату режим для страны происхождения (или None = общий режим ЕТТ)."""
    if not country:
        return None
    code = country.upper()
    active = [a for a in agreements if a.country == code and is_in_force(a, on_date)]
    if active:
        # Полноформатное соглашение важнее временного.
        active.sort(key=lambda a: (a.regime != "fta", a.in_force_from or dt.date.min))
        return active[0]
    if code in EAEU_MEMBERS:
        return TradeAgreementRef(
            country=code, regime="eaeu_member",
            agreement="Договор о Евразийском экономическом союзе от 29.05.2014",
            status="in_force",
        )
    return None


def agreement_from_row(row: dict) -> TradeAgreementRef:
    def as_date(value):
        if value is None or isinstance(value, dt.date):
            return value
        return dt.date.fromisoformat(str(value)[:10])

    return TradeAgreementRef(
        country=row["country"].upper(),
        regime=row["regime"],
        agreement=row["agreement"],
        status=row["status"],
        in_force_from=as_date(row.get("in_force_from")),
        in_force_to=as_date(row.get("in_force_to")),
        origin_proof=row.get("origin_proof"),
        legal_ref=row.get("legal_ref"),
        note=row.get("note"),
        verified=bool(row.get("verified")),
    )
