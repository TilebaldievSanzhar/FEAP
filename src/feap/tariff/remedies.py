"""Меры защиты внутреннего рынка ЕАЭС: антидемпинговые, специальные, компенсационные.

Источник данных — реестр мер ЕЭК (remedies.eaeunion.org, коллекция
zvr.v_actionsregistry). Модуль чистый:
- record_to_seed(): запись реестра → строка сида (разбор ставок из текста);
- find_measures(): какие меры могут касаться позиции (код + страна + дата);
- select_rate(): ставка с учётом страны, вида товара и производителя.

Применимость меры зависит не только от кода, но и от описания товара
(диаметр трубы, назначение и т.п.), поэтому код решения не принимает:
совпадение по коду и стране = «может применяться», а решение
(applies / not_applicable / exempt) подтверждает декларант.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

#: Тип расследования в реестре ЕЭК → вид платежа.
INVESTIGATION_KIND = {
    "AD": "antidumping",
    "SG": "special",
    "CVD": "countervailing",
    "CV": "countervailing",
}

#: Государства — члены ЕС (для мер, где страной указан 'EU').
EU_MEMBERS = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})

#: Страна в родительном падеже (как в текстах решений ЕЭК) → ISO alpha-2.
_COUNTRY_NAMES: tuple[tuple[str, str], ...] = (
    ("китайской народной республики", "CN"),
    ("азербайджанской республики", "AZ"),
    ("турецкой республики", "TR"),
    ("королевства таиланд", "TH"),
    ("республики индия", "IN"),
    ("республики узбекистан", "UZ"),
    ("европейского союза", "EU"),
    ("соединенного королевства", "GB"),
    ("украины", "UA"),
    ("японии", "JP"),
    ("малайзии", "MY"),
    ("грузии", "GE"),
    ("индии", "IN"),
    ("узбекистана", "UZ"),
)

_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_LINE_RATE_RE = re.compile(r"^[-–—•]?\s*для\s+(.+?)\s*[—–-]+\s*(\d+(?:[.,]\d+)?)\s*%")
_VARIANT_RE = re.compile(
    r"^[а-я]\)\s*для\s+(.+?)\s+(в размере|в следующих)", re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^[абвгд]\.\s", re.IGNORECASE)

#: Токены организационно-правовых форм — игнорируются при сравнении имён.
_LEGAL_TOKENS = frozenset({
    "co", "ltd", "limited", "inc", "llc", "jsc", "plc", "gmbh", "sa", "corp",
    "company", "ооо", "оао", "зао", "чао", "ао", "тоо", "пао", "ип", "осоо", "as", "aş",
})


@dataclass(frozen=True)
class RemedyRate:
    """Одна ставка меры. producers пусто = единая ставка / «прочие производители»."""

    rate_pct: Decimal
    country: str | None = None
    producers: tuple[str, ...] = ()
    variant: str | None = None


@dataclass(frozen=True)
class RemedyMeasure:
    measure_no: str
    kind: str
    title: str
    countries: tuple[str, ...]
    codes: tuple[str, ...]
    rates: tuple[RemedyRate, ...]
    status: str                      # applied | suspended | expired
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    product_description: str | None = None
    notes: str | None = None
    legal_basis: str | None = None
    needs_review: bool = False
    source_url: str | None = None


@dataclass
class RateChoice:
    rate: RemedyRate | None
    missing: list[str] = field(default_factory=list)
    note: str | None = None


# ── Сопоставление ───────────────────────────────────────────────────────────

def normalize_code(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def origin_matches(countries: tuple[str, ...] | list[str], origin: str | None) -> bool:
    if "ALL" in countries:
        return True
    if not origin:
        return False
    origin = origin.upper()
    if origin in countries:
        return True
    return "EU" in countries and (origin in EU_MEMBERS or origin == "EU")


def is_in_force(measure: RemedyMeasure, on_date: dt.date) -> bool:
    if measure.status != "applied":
        return False
    if measure.valid_from and measure.valid_from > on_date:
        return False
    return not (measure.valid_to and measure.valid_to < on_date)


def code_matches(code10: str, codes: tuple[str, ...] | list[str]) -> bool:
    return any(code10.startswith(c) for c in codes if c)


def find_measures(
    measures: list[RemedyMeasure],
    *,
    code10: str,
    origin: str | None,
    on_date: dt.date,
) -> list[RemedyMeasure]:
    """Действующие на дату меры, совпавшие по коду и стране происхождения."""
    return [
        m for m in measures
        if is_in_force(m, on_date)
        and code_matches(code10, m.codes)
        and origin_matches(m.countries, origin)
    ]


def find_code_measures(
    measures: list[RemedyMeasure], *, code10: str, on_date: dt.date,
) -> list[RemedyMeasure]:
    """Действующие меры по коду для любой страны (справка /tnved/lookup)."""
    return [m for m in measures if is_in_force(m, on_date) and code_matches(code10, m.codes)]


def _registry_order(m: RemedyMeasure) -> tuple[str, int, str]:
    prefix, _, num = m.measure_no.partition("-")
    return (prefix, int(num) if num.isdigit() else 0, m.measure_no)


def registry_measures(
    measures: list[RemedyMeasure],
    *,
    code: str | None,
    origin: str | None,
    on_date: dt.date,
) -> list[RemedyMeasure]:
    """Реестр для справочника: применяемые и приостановленные меры, не истёкшие на дату.

    code — префикс кода любой длины: «7615» находит меру на 7615101000, а
    10-значный код товара находит меру уровня позиции («7210»). Без кода и
    страны — весь реестр, по порядку номеров (AD-1, AD-3 … AD-41).
    """
    prefix = normalize_code(code or "")
    out = []
    for m in measures:
        if m.status not in ("applied", "suspended"):
            continue
        if m.valid_to and m.valid_to < on_date:
            continue
        if m.status == "applied" and m.valid_from and m.valid_from > on_date:
            continue
        if prefix and not any(c.startswith(prefix) or prefix.startswith(c) for c in m.codes if c):
            continue
        if origin and not origin_matches(m.countries, origin):
            continue
        out.append(m)
    return sorted(out, key=_registry_order)


def normalize_company(name: str) -> str:
    text = name.lower().replace("ё", "е")
    text = re.sub(r"[«»\"'`.,()\[\]/\\:;*&+-]", " ", text)
    tokens = [t for t in text.split() if t not in _LEGAL_TOKENS]
    return " ".join(tokens)


def producer_matches(manufacturer: str, producer: str) -> bool:
    a, b = normalize_company(manufacturer), normalize_company(producer)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = min(len(a), len(b))
    return shorter >= 8 and (a in b or b in a)


def select_rate(
    measure: RemedyMeasure,
    *,
    origin: str | None,
    manufacturer: str | None = None,
    variant: str | None = None,
) -> RateChoice:
    """Ставка меры для товара. Нехватка данных → missing (а не «прочие» молча)."""
    origin_up = (origin or "").upper()
    candidates = [
        r for r in measure.rates
        if r.country is None or r.country == origin_up
        or (r.country == "EU" and origin_up in EU_MEMBERS)
    ]
    if not candidates:
        return RateChoice(None, [
            f"ставка меры {measure.measure_no} для страны {origin_up or '—'} "
            "не разобрана — укажите ставку вручную (rate_pct)",
        ])

    variants = sorted({r.variant for r in candidates if r.variant})
    if variants:
        if not variant:
            return RateChoice(None, [
                f"вид товара для меры {measure.measure_no}: "
                + " | ".join(variants),
            ])
        wanted = variant.strip().lower()
        candidates = [r for r in candidates if r.variant and (
            r.variant.lower() == wanted or r.variant.lower().startswith(wanted)
        )]
        if not candidates:
            return RateChoice(None, [
                f"вид товара «{variant}» не найден в мере {measure.measure_no}: "
                + " | ".join(variants),
            ])

    named = [r for r in candidates if r.producers]
    if manufacturer:
        for r in named:
            if any(producer_matches(manufacturer, p) for p in r.producers):
                return RateChoice(r, note="индивидуальная ставка производителя")

    others = [r for r in candidates if not r.producers]
    if others:
        chosen = max(others, key=lambda r: r.rate_pct)
        note = None
        if named:
            note = ("ставка для прочих производителей" if manufacturer
                    else "производитель не указан — применена ставка для прочих производителей")
        return RateChoice(chosen, note=note)

    return RateChoice(None, [
        f"производитель товара (мера {measure.measure_no} устанавливает только "
        "индивидуальные ставки)",
    ])


# ── Разбор записи реестра ЕЭК ───────────────────────────────────────────────

def html_to_lines(value: str | None) -> list[str]:
    if not value:
        return []
    text = re.sub(r"</p>|<br\s*/?>|</li>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("​", "").replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def _find_country(text: str) -> str | None:
    low = text.lower().replace("*", "")
    for name, iso in _COUNTRY_NAMES:
        if name in low:
            return iso
    return None


def _pct(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


def split_producers(who: str) -> list[str]:
    """«A Co., Ltd., B Co., Ltd. и C» → ['A Co., Ltd.', 'B Co., Ltd.', 'C']."""
    text = re.sub(r"^(компаний|компании|группы компаний|группы)\s+", "", who.strip(),
                  flags=re.IGNORECASE)
    text = re.sub(r"\s+и\s+(?=[A-Z«\"])", ", ", text)
    names: list[str] = []
    for chunk in text.split(";"):
        tokens = [t.strip() for t in chunk.split(",")]
        merged: list[str] = []
        for token in tokens:
            if not token:
                continue
            bare = re.sub(r"[.\s]", "", token).lower()
            if merged and bare in _LEGAL_TOKENS:
                merged[-1] = f"{merged[-1]}, {token}"
            else:
                merged.append(token)
        names.extend(m.strip() for m in merged if m.strip())
    return names


@dataclass
class ParsedRates:
    rates: list[RemedyRate]
    undertakings: list[str]
    footnotes: list[str]
    needs_review: bool


def parse_rate_lines(lines: list[str]) -> ParsedRates:
    """Текст «формы меры» → ставки, ценовые обязательства, сноски, needs_review."""
    rates: list[RemedyRate] = []
    undertakings: list[str] = []
    footnotes: list[str] = []
    needs_review = False
    country: str | None = None
    variant: str | None = None
    in_undertakings = False

    for line in lines:
        low = line.lower()
        if line.startswith("*"):
            # Сноска к ставке (квоты, исключения) — в примечания, не в ставки.
            footnotes.append(line.lstrip("* ").strip())
            continue
        if "ценов" in low and "обязательств" in low:
            in_undertakings = True
            continue
        if in_undertakings:
            undertakings.append(line.lstrip("-–—• ").rstrip(";.").strip())
            continue

        if _SECTION_RE.match(line) and "пошлин" in low:
            country = _find_country(line)
            variant = None
            pct = _PCT_RE.search(line)
            if pct and "в размере" in low:
                value = _pct(pct.group(1))
                if value is not None:
                    rates.append(RemedyRate(value, country=country))
            continue

        m_var = _VARIANT_RE.match(line)
        if m_var:
            label = m_var.group(1).strip().rstrip(",")
            if m_var.group(2).lower() == "в размере":
                pct = _PCT_RE.search(line)
                value = _pct(pct.group(1)) if pct else None
                if value is None:
                    needs_review = True
                else:
                    rates.append(RemedyRate(value, country=country, variant=label))
                variant = None
            else:
                variant = label
            continue

        m_line = _LINE_RATE_RE.match(line)
        if m_line:
            who = m_line.group(1).strip()
            value = _pct(m_line.group(2))
            if value is None:
                needs_review = True
                continue
            who_low = who.lower()
            line_country = _find_country(who)
            if "прочих производителей" in who_low or re.match(r"^производителей\b", who_low):
                rates.append(RemedyRate(value, country=line_country or country, variant=variant))
            elif line_country and "происходящ" in who_low:
                rates.append(RemedyRate(value, country=line_country, variant=variant))
            else:
                rates.append(RemedyRate(
                    value, country=line_country or country,
                    producers=tuple(split_producers(who)), variant=variant,
                ))
            continue

        if "в размерах" in low and _PCT_RE.search(line):
            needs_review = True  # «в размерах от X % до Y %» — нужен ручной разбор
            continue
        if "в размере" in low and _PCT_RE.search(line):
            value = _pct(_PCT_RE.search(line).group(1))
            if value is not None:
                rates.append(RemedyRate(value, country=country, variant=variant))
            continue

        if _PCT_RE.search(line):
            needs_review = True

    if not rates:
        needs_review = True
    return ParsedRates(rates, undertakings, footnotes, needs_review)


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def registry_status(record: dict, today: dt.date) -> str:
    """statuscode 1 → applied; 2 + actual → suspended (временное неприменение); иначе expired."""
    end = _date(record.get("enddate"))
    if end and end < today:
        return "expired"
    code = str(record.get("statuscode") or "")
    if code == "1":
        return "applied"
    if code == "2" and record.get("actual"):
        return "suspended"
    return "expired"


def record_to_seed(record: dict, *, today: dt.date) -> dict:
    """Запись zvr.v_actionsregistry → строка data/trade_remedies_seed.json."""
    lines = html_to_lines(record.get("actiondescription"))
    parsed = parse_rate_lines(lines)
    rates = parsed.rates
    notes_parts = [" ".join(html_to_lines(record.get("note")))]
    notes_parts.extend(parsed.footnotes)
    if parsed.undertakings:
        notes_parts.append("Ценовые обязательства: " + "; ".join(parsed.undertakings))
    notes = " ".join(p for p in notes_parts if p).strip() or None

    codes: list[str] = []
    for raw in record.get("tnved") or []:
        code = normalize_code(raw)
        if code and code not in codes:
            codes.append(code)

    oid = (record.get("_id") or {}).get("$oid")
    start, end = _date(record.get("startdate")), _date(record.get("enddate"))
    return {
        "measure_no": record.get("investigationnumber"),
        "kind": INVESTIGATION_KIND.get(str(record.get("investigationtype") or "").upper(),
                                       "antidumping"),
        "title": " ".join(html_to_lines(record.get("shortname"))) or record.get("investigationnumber"),
        "product_description": " ".join(html_to_lines(record.get("description"))) or None,
        "countries": [c.upper() for c in (record.get("exportingcountrycode") or [])],
        "codes": codes,
        "rates": [
            {
                "rate_pct": format(r.rate_pct, "f"),
                "country": r.country,
                "producers": list(r.producers),
                "variant": r.variant,
            }
            for r in rates
        ],
        "rate_text": "\n".join(lines),
        "notes": notes,
        "legal_basis": "\n".join(html_to_lines(record.get("foundation"))) or None,
        "status": registry_status(record, today),
        "valid_from": start.isoformat() if start else None,
        "valid_to": end.isoformat() if end else None,
        "needs_review": parsed.needs_review,
        "source_url": (
            f"https://remedies.eaeunion.org/dimd/ru/security/actions/card/{oid}" if oid else None
        ),
        "source_modified": record.get("modified"),
    }


def measure_from_row(row: dict) -> RemedyMeasure:
    """Строка сида/БД (dict) → RemedyMeasure."""
    rates = tuple(
        RemedyRate(
            rate_pct=Decimal(str(r["rate_pct"])),
            country=r.get("country"),
            producers=tuple(r.get("producers") or ()),
            variant=r.get("variant"),
        )
        for r in (row.get("rates") or [])
    )

    def as_date(value):
        if value is None or isinstance(value, dt.date):
            return value
        return _date(str(value))

    return RemedyMeasure(
        measure_no=row["measure_no"],
        kind=row["kind"],
        title=row["title"],
        countries=tuple(row.get("countries") or ()),
        codes=tuple(row.get("codes") or ()),
        rates=rates,
        status=row["status"],
        valid_from=as_date(row.get("valid_from")),
        valid_to=as_date(row.get("valid_to")),
        product_description=row.get("product_description"),
        notes=row.get("notes"),
        legal_basis=row.get("legal_basis"),
        needs_review=bool(row.get("needs_review")),
        source_url=row.get("source_url"),
    )
