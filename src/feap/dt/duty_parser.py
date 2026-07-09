"""Парсер текстовых ставок ввозной пошлины ЕТТ (`tnved_tree.duty_rate`).

Грамматика зафиксирована по полному корпусу leaves.json
(13 279 листьев, 254 уникальных строки — docs/dt_core_dev_plan.md §5):

    ''                                      -> NONE
    '{p}%'                                  -> AD_VALOREM
    '{p}%, но не менее {a} {CUR} за {UNIT}' -> COMBINED_MIN   (max из двух частей)
    '{p}% плюс {a} {CUR} за {UNIT}'         -> COMBINED_PLUS  (сумма частей)
    '{a} {CUR} за {UNIT}'                   -> SPECIFIC

    CUR  ∈ {EUR, USD}
    UNIT ∈ {1 кг, 1 л, 1 см3, мл, 1 т, 1 шт, 1 пар, 1 1000 шт, 1 м2}

Неизвестный формат — это ошибка данных, а не повод молча вернуть 0:
парсер бросает DutyParseError, смоук-тест гоняет его по всему корпусу.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class DutyKind(str, Enum):
    NONE = "none"
    AD_VALOREM = "ad_valorem"
    SPECIFIC = "specific"
    COMBINED_MIN = "combined_min"
    COMBINED_PLUS = "combined_plus"


class BasisKind(str, Enum):
    """Физическая база специфической части ставки."""

    NET_KG = "net_kg"                  # за 1 кг (вес нетто)
    NET_TON = "net_ton"                # за 1 т (вес нетто / 1000)
    LITRE = "litre"                    # за 1 л (доп. единица)
    CM3 = "cm3"                        # за 1 см3/мл (объём двигателя и т.п.)
    PIECE = "piece"                    # за 1 шт
    PAIR = "pair"                      # за 1 пар
    THOUSAND_PIECES = "thousand_pieces"  # за 1000 шт
    M2 = "m2"                          # за 1 м2


#: Что спросить у пользователя, если базы нет в данных позиции.
BASIS_MISSING_LABEL: dict[BasisKind, str] = {
    BasisKind.NET_KG: "вес нетто (кг)",
    BasisKind.NET_TON: "вес нетто (кг)",
    BasisKind.LITRE: "объём в литрах (доп. единица)",
    BasisKind.CM3: "объём в см3/мл (доп. единица)",
    BasisKind.PIECE: "количество в штуках (доп. единица)",
    BasisKind.PAIR: "количество пар (доп. единица)",
    BasisKind.THOUSAND_PIECES: "количество в штуках (доп. единица)",
    BasisKind.M2: "площадь в м2 (доп. единица)",
}

_UNIT_MAP: dict[str, BasisKind] = {
    "1 кг": BasisKind.NET_KG,
    "1 т": BasisKind.NET_TON,
    "1 л": BasisKind.LITRE,
    "1 см3, мл": BasisKind.CM3,
    "1 шт": BasisKind.PIECE,
    "1 пар": BasisKind.PAIR,
    "1 1000 шт": BasisKind.THOUSAND_PIECES,
    "1 м2": BasisKind.M2,
}


@dataclass(frozen=True)
class ParsedDuty:
    kind: DutyKind
    advalorem_pct: Decimal | None = None
    specific_amount: Decimal | None = None
    specific_currency: str | None = None   # 'EUR' | 'USD'
    basis: BasisKind | None = None
    raw: str = ""

    @property
    def has_specific(self) -> bool:
        return self.kind in (DutyKind.SPECIFIC, DutyKind.COMBINED_MIN, DutyKind.COMBINED_PLUS)


class DutyParseError(ValueError):
    """Строка ставки не соответствует известной грамматике ЕТТ."""


_NUM = r"\d+(?:\.\d+)?"
_RE_AD_VALOREM = re.compile(rf"^({_NUM})\s*%$")
_RE_COMBINED_MIN = re.compile(
    rf"^({_NUM})\s*%,\s*но не менее\s+({_NUM})\s+(EUR|USD)\s+за\s+(.+)$"
)
_RE_COMBINED_PLUS = re.compile(
    rf"^({_NUM})\s*%\s+плюс\s+({_NUM})\s+(EUR|USD)\s+за\s+(.+)$"
)
_RE_SPECIFIC = re.compile(rf"^({_NUM})\s+(EUR|USD)\s+за\s+(.+)$")


def _basis(unit_text: str, raw: str) -> BasisKind:
    unit = re.sub(r"\s+", " ", unit_text).strip().rstrip(".")
    try:
        return _UNIT_MAP[unit]
    except KeyError:
        raise DutyParseError(f"Неизвестная единица ставки: {unit!r} в {raw!r}") from None


def parse_duty_rate(raw: str | None) -> ParsedDuty:
    """Разобрать строку ставки ЕТТ. Пустая строка/None → DutyKind.NONE."""
    s = re.sub(r"\s+", " ", (raw or "")).strip()
    if not s:
        return ParsedDuty(kind=DutyKind.NONE, raw=s)

    if m := _RE_AD_VALOREM.match(s):
        return ParsedDuty(
            kind=DutyKind.AD_VALOREM, advalorem_pct=Decimal(m.group(1)), raw=s,
        )

    if m := _RE_COMBINED_MIN.match(s):
        return ParsedDuty(
            kind=DutyKind.COMBINED_MIN,
            advalorem_pct=Decimal(m.group(1)),
            specific_amount=Decimal(m.group(2)),
            specific_currency=m.group(3),
            basis=_basis(m.group(4), s),
            raw=s,
        )

    if m := _RE_COMBINED_PLUS.match(s):
        return ParsedDuty(
            kind=DutyKind.COMBINED_PLUS,
            advalorem_pct=Decimal(m.group(1)),
            specific_amount=Decimal(m.group(2)),
            specific_currency=m.group(3),
            basis=_basis(m.group(4), s),
            raw=s,
        )

    if m := _RE_SPECIFIC.match(s):
        return ParsedDuty(
            kind=DutyKind.SPECIFIC,
            specific_amount=Decimal(m.group(1)),
            specific_currency=m.group(2),
            basis=_basis(m.group(3), s),
            raw=s,
        )

    raise DutyParseError(f"Неизвестный формат ставки ЕТТ: {raw!r}")
