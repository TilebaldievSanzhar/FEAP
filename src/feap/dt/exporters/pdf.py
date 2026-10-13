"""PDF-экспорт печатной формы проекта ДТ.

Это не факсимиле бланка ДТ, а читаемая печатная форма: шапка по графам,
блок на каждый товар, исчисление платежей и результаты ФЛК. На каждой
странице — диагональный водяной знак «ПРОЕКТ» (документ не для подачи).

Кириллица требует TTF-шрифта: reportlab из коробки умеет только latin-1.
Порядок поиска: settings.DT_PDF_FONT_PATH → системные пути (Arial на
Windows, DejaVu Sans на Linux/macOS). Если шрифт не найден — PdfFontError.
"""

from __future__ import annotations

import io
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from feap.config import settings
from feap.dt.exporters.xlsx import HEADER_LABELS, PAYMENT_KIND_RU, _fmt

FONT = "DTSans"
FONT_BOLD = "DTSans-Bold"

_REGULAR_CANDIDATES = (
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
_BOLD_CANDIDATES = (
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


class PdfFontError(RuntimeError):
    """Не найден TTF-шрифт с кириллицей."""


def _find_font(explicit: str, candidates: tuple[str, ...]) -> str | None:
    for path in ((explicit,) if explicit else ()) + candidates:
        if path and Path(path).is_file():
            return path
    return None


@lru_cache(maxsize=1)
def ensure_fonts() -> tuple[str, str]:
    """Зарегистрировать шрифты один раз на процесс. Возвращает (regular, bold)."""
    regular = _find_font(settings.DT_PDF_FONT_PATH.strip(), _REGULAR_CANDIDATES)
    if regular is None:
        raise PdfFontError(
            "Не найден TTF-шрифт с кириллицей для PDF. Укажите путь в "
            "DT_PDF_FONT_PATH (например, DejaVuSans.ttf) или установите шрифты."
        )
    bold = _find_font(settings.DT_PDF_FONT_BOLD_PATH.strip(), _BOLD_CANDIDATES) or regular
    pdfmetrics.registerFont(TTFont(FONT, regular))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, bold))
    return FONT, FONT_BOLD


# ── Стили ────────────────────────────────────────────────────────────────────

_GRID = colors.Color(0.75, 0.78, 0.82)
_HEAD_BG = colors.Color(0.87, 0.92, 0.97)
_WARN_BG = colors.Color(1.0, 0.95, 0.8)
_ERR_BG = colors.Color(0.99, 0.89, 0.93)
_MUTED = colors.Color(0.35, 0.35, 0.35)

_KIND_RU = PAYMENT_KIND_RU
_SEV_RU = {"error": "ОШИБКА", "warning": "Предупреждение", "info": "Инфо"}


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("title", fontName=FONT_BOLD, fontSize=14, leading=18,
                                alignment=TA_CENTER, spaceAfter=2 * mm),
        "meta": ParagraphStyle("meta", fontName=FONT, fontSize=8.5, leading=11,
                               alignment=TA_CENTER, textColor=_MUTED, spaceAfter=4 * mm),
        "h2": ParagraphStyle("h2", fontName=FONT_BOLD, fontSize=11, leading=14,
                             spaceBefore=5 * mm, spaceAfter=2 * mm),
        "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8.5, leading=11),
        "cell_b": ParagraphStyle("cell_b", fontName=FONT_BOLD, fontSize=8.5, leading=11),
    }


def _money(value: Any) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{Decimal(str(value)):,.2f}".replace(",", " ")
    except (InvalidOperation, ValueError):
        return str(value)


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    escaped = (
        str(text if text not in (None, "") else "—")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
    return Paragraph(escaped, style)


def _table(data: list[list[Any]], widths: list[float], *, header: bool = True,
           row_bg: list[tuple[int, colors.Color]] | None = None) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG))
    for row_idx, bg in row_bg or []:
        style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))
    table.setStyle(TableStyle(style))
    return table


# ── Водяной знак и колонтитул ────────────────────────────────────────────────

def _on_page(canvas, doc) -> None:  # noqa: ANN001 — сигнатура reportlab
    canvas.saveState()
    canvas.setFont(FONT_BOLD, 72)
    canvas.setFillColor(colors.Color(0.55, 0.55, 0.55, alpha=0.16))
    canvas.translate(A4[0] / 2, A4[1] / 2)
    canvas.rotate(45)
    canvas.drawCentredString(0, 0, "ПРОЕКТ")
    canvas.restoreState()

    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawString(15 * mm, 8 * mm, "Проект ДТ · сформировано FEAP · не для подачи")
    canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Лист {canvas.getPageNumber()}")
    canvas.restoreState()


# ── Рендер ───────────────────────────────────────────────────────────────────

def render_pdf(graphs: dict, findings: list[dict]) -> bytes:
    ensure_fonts()
    st = _styles()

    header: dict = graphs.get("header", {})
    items: list[dict] = graphs.get("items", [])
    totals: dict = graphs.get("totals", {})
    meta: dict = graphs.get("meta", {})

    story: list[Any] = [
        _p("ПРОЕКТ ДЕКЛАРАЦИИ НА ТОВАРЫ", st["title"]),
        _p(
            f"не для подачи · процедура {meta.get('procedure', '')} · "
            f"структура {meta.get('structure_version', '')} · "
            f"курс на {meta.get('rate_date', '')}",
            st["meta"],
        ),
    ]

    # Шапка по графам
    head_rows: list[list[Any]] = [[_p("Графа", st["cell_b"]), _p("Значение", st["cell_b"])]]
    for key, label in HEADER_LABELS:
        if key in header:
            head_rows.append([_p(label, st["cell"]), _p(_fmt(header.get(key)), st["cell"])])
    story.append(_table(head_rows, [70 * mm, 110 * mm]))

    # Товары
    for item in items:
        g31 = item.get("g31") or {}
        g41 = item.get("g41") or {}
        supp = ""
        if g41.get("qty"):
            supp = f"{g41['qty']} {g41.get('unit') or g41.get('required_unit') or ''}".strip()
        elif g41.get("required_unit"):
            supp = f"требуется: {g41['required_unit']}"

        block: list[Any] = [
            _p(
                f"Товар {item.get('g32')} · код ТН ВЭД (33): "
                f"{item.get('g33') or '⚠ нет кода'}",
                st["h2"],
            ),
            _p(g31.get("description") or "—", st["cell"]),
            *([_p(f"Производитель: {g31['manufacturer']}", st["cell"])]
              if g31.get("manufacturer") else []),
            Spacer(0, 1.5 * mm),
            _table(
                [
                    [_p(t, st["cell_b"]) for t in
                     ["Происх. (34)", "Брутто, кг (35)", "Нетто, кг (38)",
                      "Доп. ед. (41)", "Цена (42)", "ТС, сом (45)", "Стат., USD (46)"]],
                    [_p(item.get("g34"), st["cell"]),
                     _p(_money(item.get("g35")), st["cell"]),
                     _p(_money(item.get("g38")), st["cell"]),
                     _p(supp or "—", st["cell"]),
                     _p(_money(item.get("g42")), st["cell"]),
                     _p(_money(item.get("g45")), st["cell"]),
                     _p(_money(item.get("g46")), st["cell"])],
                ],
                [22 * mm, 26 * mm, 26 * mm, 28 * mm, 26 * mm, 27 * mm, 25 * mm],
            ),
        ]

        payments = item.get("g47", [])
        if payments:
            pay_rows: list[list[Any]] = [[
                _p(t, st["cell_b"])
                for t in ["Вид", "Код", "Основа", "Ставка", "Сумма, сом", "Примечание"]
            ]]
            warn_rows: list[tuple[int, colors.Color]] = []
            for p in payments:
                note = p.get("note") or ""
                if p.get("missing"):
                    note = ("; ".join(p["missing"]) + (f" · {note}" if note else "")).strip()
                    warn_rows.append((len(pay_rows), _WARN_BG))
                pay_rows.append([
                    _p(_KIND_RU.get(p.get("kind"), p.get("kind")), st["cell"]),
                    _p(p.get("code"), st["cell"]),
                    _p(_money(p.get("basis")), st["cell"]),
                    _p(p.get("rate"), st["cell"]),
                    _p(_money(p.get("amount")) if p.get("amount") is not None
                       else "не рассчитано", st["cell"]),
                    _p(note or "—", st["cell"]),
                ])
            block.append(Spacer(0, 1.5 * mm))
            block.append(_table(
                pay_rows, [22 * mm, 12 * mm, 27 * mm, 47 * mm, 27 * mm, 45 * mm],
                row_bg=warn_rows,
            ))
        story.append(KeepTogether(block))

    # Итоги
    story.append(_p("Итого по декларации", st["h2"]))
    total_rows: list[list[Any]] = [
        [_p("Общая таможенная стоимость (12), сом", st["cell"]),
         _p(_money(totals.get("g12")), st["cell"])],
    ]
    total_all = Decimal(0)
    for kind, amount in (totals.get("payments") or {}).items():
        total_rows.append([
            _p(_KIND_RU.get(kind, kind), st["cell"]), _p(_money(amount), st["cell"]),
        ])
        try:
            total_all += Decimal(str(amount))
        except (InvalidOperation, ValueError):
            pass
    total_rows.append([_p("ВСЕГО платежей, сом", st["cell_b"]),
                       _p(_money(total_all), st["cell_b"])])
    story.append(_table(total_rows, [110 * mm, 70 * mm], header=False))

    # Проверки ФЛК
    story.append(_p("Проверки (ФЛК)", st["h2"]))
    if findings:
        find_rows: list[list[Any]] = [[
            _p(t, st["cell_b"]) for t in ["Правило", "Уровень", "Графа", "Сообщение"]
        ]]
        row_bg: list[tuple[int, colors.Color]] = []
        for f in findings:
            severity = f.get("severity")
            if severity == "error":
                row_bg.append((len(find_rows), _ERR_BG))
            elif severity == "warning":
                row_bg.append((len(find_rows), _WARN_BG))
            find_rows.append([
                _p(f.get("rule"), st["cell"]),
                _p(_SEV_RU.get(severity, severity), st["cell"]),
                _p(f.get("graph"), st["cell"]),
                _p(f.get("message"), st["cell"]),
            ])
        story.append(_table(find_rows, [18 * mm, 28 * mm, 26 * mm, 108 * mm],
                            row_bg=row_bg))
    else:
        story.append(_p("Замечаний нет ✔", st["cell"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title="Проект декларации на товары (не для подачи)",
        author="FEAP",
    )
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
