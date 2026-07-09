"""Пересчёт итогов извлечённых документов — только код, никакого доверия LLM.

totals_selfcheck в схемах — это самопроверка модели (SGR verification step),
она полезна для качества генерации, но НЕ является контролем. Контроль — здесь:
Σ строк сверяется с заявленным итогом через Decimal; расхождение → DocCheck
со статусом mismatch (аналог claim-vs-action валидатора).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from feap.dt.extractors.schemas import (
    DocCheck,
    InvoiceExtraction,
    PackingExtraction,
)

#: Допуск расхождения сумм, % (синхронно с ФЛК F-04).
TOLERANCE_PCT = Decimal("0.5")

_NUM_CLEAN_RE = re.compile(r"[\s ']")


def parse_num(raw: str | None) -> Decimal | None:
    """'1 234,56' / '1234.56' → Decimal. Мусор → None (не ноль!)."""
    if raw is None:
        return None
    cleaned = _NUM_CLEAN_RE.sub("", str(raw)).replace(",", ".")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _fmt(value: Decimal | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _within_tolerance(expected: Decimal, actual: Decimal) -> bool:
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / abs(expected) * 100 <= TOLERANCE_PCT


def _sum_check(
    check_id: str,
    label: str,
    parts: list[Decimal | None],
    stated: Decimal | None,
) -> DocCheck:
    known = [p for p in parts if p is not None]
    if stated is None or not known:
        return DocCheck(
            check=check_id, status="skipped",
            message=f"{label}: итог или строки не извлечены — сверка пропущена",
        )
    total = sum(known, Decimal(0))
    if len(known) < len(parts):
        note = f" (в {len(parts) - len(known)} строках значение не извлечено)"
    else:
        note = ""
    if _within_tolerance(stated, total):
        return DocCheck(
            check=check_id, status="ok",
            message=f"{label}: сумма строк сходится с итогом{note}",
            expected=_fmt(stated), actual=_fmt(total),
        )
    return DocCheck(
        check=check_id, status="mismatch",
        message=f"{label}: сумма строк ({_fmt(total)}) расходится с итогом "
                f"документа ({_fmt(stated)}) более чем на {TOLERANCE_PCT}%{note}",
        expected=_fmt(stated), actual=_fmt(total),
    )


# ── Инвойс ───────────────────────────────────────────────────────────────────

def check_invoice(doc: InvoiceExtraction) -> list[DocCheck]:
    checks: list[DocCheck] = []

    if not doc.lines:
        checks.append(DocCheck(
            check="D-INV-00", status="mismatch",
            message="В инвойсе не извлечено ни одной товарной строки",
        ))

    checks.append(_sum_check(
        "D-INV-01", "Инвойс",
        [parse_num(line.amount) for line in doc.lines],
        parse_num(doc.total_amount),
    ))

    # Построчно: qty × unit_price vs amount
    bad_lines: list[str] = []
    for line in doc.lines:
        qty, price, amount = parse_num(line.qty), parse_num(line.unit_price), parse_num(line.amount)
        if qty is None or price is None or amount is None:
            continue
        if not _within_tolerance(amount, qty * price):
            bad_lines.append(
                f"строка {line.line_no}: {_fmt(qty)}×{_fmt(price)}={_fmt(qty * price)} ≠ {_fmt(amount)}"
            )
    if bad_lines:
        checks.append(DocCheck(
            check="D-INV-02", status="mismatch",
            message="Количество × цена расходится с суммой строки: " + "; ".join(bad_lines),
        ))
    else:
        checks.append(DocCheck(
            check="D-INV-02", status="ok",
            message="Построчная арифметика (кол-во × цена = сумма) сходится",
        ))

    if doc.currency and not re.fullmatch(r"[A-Z]{3}", doc.currency):
        checks.append(DocCheck(
            check="D-INV-03", status="mismatch",
            message=f"Валюта инвойса «{doc.currency}» — не ISO-код из 3 букв",
            actual=doc.currency,
        ))

    return checks


# ── Упаковочный лист ─────────────────────────────────────────────────────────

def check_packing(doc: PackingExtraction) -> list[DocCheck]:
    checks: list[DocCheck] = []

    if not doc.lines:
        checks.append(DocCheck(
            check="D-PCK-00", status="mismatch",
            message="В упаковочном листе не извлечено ни одной строки",
        ))

    checks.append(_sum_check(
        "D-PCK-01", "Упаковочный (нетто)",
        [parse_num(line.net_kg) for line in doc.lines],
        parse_num(doc.net_total_kg),
    ))
    checks.append(_sum_check(
        "D-PCK-02", "Упаковочный (брутто)",
        [parse_num(line.gross_kg) for line in doc.lines],
        parse_num(doc.gross_total_kg),
    ))

    net_t, gross_t = parse_num(doc.net_total_kg), parse_num(doc.gross_total_kg)
    if net_t is not None and gross_t is not None and net_t > gross_t:
        checks.append(DocCheck(
            check="D-PCK-03", status="mismatch",
            message=f"Итого нетто ({_fmt(net_t)}) больше брутто ({_fmt(gross_t)})",
            expected=_fmt(gross_t), actual=_fmt(net_t),
        ))

    pkg_parts = [line.packages for line in doc.lines]
    known_pkgs = [p for p in pkg_parts if p is not None]
    if doc.packages_total is not None and known_pkgs and len(known_pkgs) == len(pkg_parts):
        total = sum(known_pkgs)
        if total != doc.packages_total:
            checks.append(DocCheck(
                check="D-PCK-04", status="mismatch",
                message=f"Σ мест по строкам ({total}) ≠ итогу мест ({doc.packages_total})",
                expected=str(doc.packages_total), actual=str(total),
            ))
        else:
            checks.append(DocCheck(
                check="D-PCK-04", status="ok", message="Σ мест по строкам сходится с итогом",
                expected=str(doc.packages_total), actual=str(total),
            ))

    return checks


def run_checks(kind: str, doc: BaseModel) -> list[DocCheck]:
    """Диспетчер проверок по типу документа. Типы без числовых итогов — пусто."""
    if kind == "invoice" and isinstance(doc, InvoiceExtraction):
        return check_invoice(doc)
    if kind == "packing" and isinstance(doc, PackingExtraction):
        return check_packing(doc)
    return []
