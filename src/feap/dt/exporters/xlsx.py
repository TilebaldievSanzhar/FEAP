"""XLSX-экспорт проекта ДТ «по графам».

Файл оптимизирован под ручной перенос в АРМ «Брокер»: лист «ДТ (шапка)» идёт
в порядке граф, товары — таблицей, платежи — построчно как в графе 47,
проверки — отдельным листом. Водяная пометка «ПРОЕКТ» — в заголовке каждого
листа (это не документ для подачи).
"""

from __future__ import annotations

import io
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

HEADER_LABELS: list[tuple[str, str]] = [
    ("g01", "1. Декларация"),
    ("g02", "2. Отправитель / Экспортёр"),
    ("g03", "3. Формы (листы)"),
    ("g05", "5. Всего товаров"),
    ("g06", "6. Всего мест"),
    ("g07", "7. Справочный номер"),
    ("g08", "8. Получатель"),
    ("g09", "9. Лицо, отв. за фин. урегулирование"),
    ("g11", "11. Торгующая страна"),
    ("g12", "12. Общая таможенная стоимость, сом"),
    ("g14", "14. Декларант / Представитель"),
    ("g15", "15. Страна отправления"),
    ("g17", "17. Страна назначения"),
    ("g18", "18. Транспорт при отправлении"),
    ("g19", "19. Контейнер"),
    ("g20", "20. Условия поставки"),
    ("g21", "21. Транспорт на границе"),
    ("g22", "22. Валюта и общая сумма по счёту"),
    ("g23", "23. Курс валюты"),
    ("g25", "25. Вид транспорта на границе"),
    ("g37", "37. Процедура"),
]

_TITLE_FONT = Font(bold=True, size=12)
_HEAD_FONT = Font(bold=True)
_HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
_WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
_ERR_FILL = PatternFill("solid", fgColor="FCE4EC")
_WRAP = Alignment(wrap_text=True, vertical="top")


def _fmt(value: Any) -> Any:
    """Значение графы → читаемая ячейка."""
    if value is None:
        return ""
    if isinstance(value, dict):
        if {"name"} <= set(value):  # сторона сделки
            parts = [value.get("name")]
            if value.get("inn"):
                parts.append(f"ИНН {value['inn']}")
            if value.get("address"):
                parts.append(value["address"])
            if value.get("country"):
                parts.append(value["country"])
            return "\n".join(str(p) for p in parts if p)
        if {"incoterms"} <= set(value):
            return " ".join(str(p) for p in (value.get("incoterms"), value.get("place")) if p)
        if {"currency", "amount"} <= set(value):
            return f"{value.get('currency')} {value.get('amount')}"
        if {"rate", "date"} <= set(value):
            return f"{value.get('rate')} ({value.get('date')})"
        if {"direction", "procedure"} <= set(value):
            return f"{value.get('direction')} {value.get('procedure')} {value.get('form', '')}".strip()
        if {"sheets"} <= set(value):
            return value.get("sheets")
        if {"requested"} <= set(value):
            return f"{value.get('requested')}{value.get('previous')}{value.get('feature')}"
        return "; ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, ""))
    return value


def _num(value: Any) -> Any:
    """Строка-Decimal → float для числовых ячеек (только отображение)."""
    if value in (None, ""):
        return ""
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return value


def _head_row(ws: Worksheet, row: int, headers: list[str]) -> None:
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = _HEAD_FONT
        cell.fill = _HEAD_FILL


def _autosize(ws: Worksheet, widths: dict[int, int]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width


def render_xlsx(graphs: dict, findings: list[dict]) -> bytes:
    header: dict = graphs.get("header", {})
    items: list[dict] = graphs.get("items", [])
    totals: dict = graphs.get("totals", {})
    meta: dict = graphs.get("meta", {})

    wb = Workbook()

    # ── Лист 1: шапка ДТ ────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "ДТ (шапка)"
    ws.cell(row=1, column=1, value="ПРОЕКТ ДЕКЛАРАЦИИ НА ТОВАРЫ — не для подачи").font = _TITLE_FONT
    ws.cell(row=2, column=1, value=f"Процедура {meta.get('procedure', '')} · структура "
                                   f"{meta.get('structure_version', '')} · курс на {meta.get('rate_date', '')}")
    _head_row(ws, 4, ["Графа", "Значение"])
    r = 5
    for key, label in HEADER_LABELS:
        if key not in header:
            continue
        ws.cell(row=r, column=1, value=label).alignment = _WRAP
        cell = ws.cell(row=r, column=2, value=_fmt(header.get(key)))
        cell.alignment = _WRAP
        r += 1
    _autosize(ws, {1: 42, 2: 60})
    ws.freeze_panes = "A5"

    # ── Лист 2: товары ──────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Товары")
    ws2.cell(row=1, column=1, value="ПРОЕКТ · Товары (графы 31–47)").font = _TITLE_FONT
    cols = [
        "№ (32)", "Код ТН ВЭД (33)", "Описание (31)", "Происх. (34)",
        "Брутто, кг (35)", "Нетто, кг (38)", "Доп. ед. (41)",
        "Цена (42)", "Тамож. стоимость, сом (45)", "Стат. стоимость, USD (46)",
        "Пошлина, сом", "Акциз, сом", "НДС, сом",
    ]
    _head_row(ws2, 3, cols)
    for i, item in enumerate(items):
        row = 4 + i
        g31 = item.get("g31") or {}
        g41 = item.get("g41") or {}
        payments = {p.get("kind"): p for p in item.get("g47", [])}

        def pay_amount(kind: str) -> Any:
            p = payments.get(kind)
            if not p:
                return ""
            return _num(p.get("amount")) if p.get("amount") is not None else "⚠ не рассчитано"

        supp = ""
        if g41.get("qty"):
            supp = f"{g41['qty']} {g41.get('unit') or g41.get('required_unit') or ''}".strip()
        elif g41.get("required_unit"):
            supp = f"⚠ требуется: {g41['required_unit']}"

        values = [
            item.get("g32"), item.get("g33") or "⚠ нет кода", g31.get("description"),
            item.get("g34"), _num(item.get("g35")), _num(item.get("g38")), supp,
            _num(item.get("g42")), _num(item.get("g45")), _num(item.get("g46")),
            pay_amount("duty"), pay_amount("excise"), pay_amount("vat"),
        ]
        for col, value in enumerate(values, start=1):
            cell = ws2.cell(row=row, column=col, value=value)
            if col == 3:
                cell.alignment = _WRAP
            if isinstance(value, float):
                cell.number_format = "#,##0.00"
    _autosize(ws2, {1: 7, 2: 15, 3: 50, 4: 9, 5: 12, 6: 12, 7: 18,
                    8: 12, 9: 18, 10: 18, 11: 14, 12: 12, 13: 14})
    ws2.freeze_panes = "A4"

    # ── Лист 3: платежи (графа 47) ──────────────────────────────────────────
    ws3 = wb.create_sheet("Платежи (47)")
    ws3.cell(row=1, column=1, value="ПРОЕКТ · Исчисление платежей").font = _TITLE_FONT
    _head_row(ws3, 3, ["Товар №", "Вид", "Код", "Основа", "Ставка", "Сумма, сом", "Примечание"])
    r = 4
    kind_ru = {"duty": "Пошлина", "excise": "Акциз", "vat": "НДС", "fee": "Сбор"}
    for item in items:
        for p in item.get("g47", []):
            note = p.get("note") or ""
            if p.get("missing"):
                note = ("; ".join(p["missing"]) + (f" · {note}" if note else "")).strip()
            ws3.cell(row=r, column=1, value=item.get("g32"))
            ws3.cell(row=r, column=2, value=kind_ru.get(p.get("kind"), p.get("kind")))
            ws3.cell(row=r, column=3, value=p.get("code"))
            ws3.cell(row=r, column=4, value=_num(p.get("basis")))
            ws3.cell(row=r, column=5, value=p.get("rate"))
            amount_cell = ws3.cell(row=r, column=6, value=_num(p.get("amount")) if p.get("amount") is not None else "не рассчитано")
            if p.get("missing"):
                for c in range(1, 8):
                    ws3.cell(row=r, column=c).fill = _WARN_FILL
            if isinstance(amount_cell.value, float):
                amount_cell.number_format = "#,##0.00"
            ws3.cell(row=r, column=7, value=note).alignment = _WRAP
            r += 1
    fee_line = totals.get("fee_line")
    if fee_line:
        ws3.cell(row=r, column=1, value="—")
        ws3.cell(row=r, column=2, value="Сбор за оформление")
        ws3.cell(row=r, column=3, value=fee_line.get("code"))
        ws3.cell(row=r, column=5, value=fee_line.get("rate"))
        ws3.cell(row=r, column=6, value=_num(fee_line.get("amount")))
        ws3.cell(row=r, column=7, value=fee_line.get("note") or "").alignment = _WRAP
        r += 1
    r += 1
    ws3.cell(row=r, column=5, value="ИТОГО:").font = _HEAD_FONT
    total_all = Decimal(0)
    for kind, amount in (totals.get("payments") or {}).items():
        r += 1
        ws3.cell(row=r, column=2, value=kind_ru.get(kind, kind))
        cell = ws3.cell(row=r, column=6, value=_num(amount))
        if isinstance(cell.value, float):
            cell.number_format = "#,##0.00"
        try:
            total_all += Decimal(str(amount))
        except (InvalidOperation, ValueError):
            pass
    r += 1
    ws3.cell(row=r, column=2, value="ВСЕГО").font = _HEAD_FONT
    cell = ws3.cell(row=r, column=6, value=float(total_all))
    cell.font = _HEAD_FONT
    cell.number_format = "#,##0.00"
    _autosize(ws3, {1: 9, 2: 20, 3: 8, 4: 16, 5: 34, 6: 16, 7: 50})
    ws3.freeze_panes = "A4"

    # ── Лист 4: проверки ────────────────────────────────────────────────────
    ws4 = wb.create_sheet("Проверки")
    ws4.cell(row=1, column=1, value="Результаты ФЛК и предупреждения").font = _TITLE_FONT
    _head_row(ws4, 3, ["Правило", "Уровень", "Графа", "Сообщение"])
    sev_ru = {"error": "ОШИБКА", "warning": "Предупреждение", "info": "Инфо"}
    for i, f in enumerate(findings):
        row = 4 + i
        ws4.cell(row=row, column=1, value=f.get("rule"))
        ws4.cell(row=row, column=2, value=sev_ru.get(f.get("severity"), f.get("severity")))
        ws4.cell(row=row, column=3, value=f.get("graph"))
        ws4.cell(row=row, column=4, value=f.get("message")).alignment = _WRAP
        if f.get("severity") == "error":
            for c in range(1, 5):
                ws4.cell(row=row, column=c).fill = _ERR_FILL
        elif f.get("severity") == "warning":
            for c in range(1, 5):
                ws4.cell(row=row, column=c).fill = _WARN_FILL
    if not findings:
        ws4.cell(row=4, column=1, value="Замечаний нет ✔")
    _autosize(ws4, {1: 12, 2: 16, 3: 14, 4: 90})
    ws4.freeze_panes = "A4"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
