"""PDF паспорта товара: код и обоснование, ставки и меры, платежи, себестоимость.

Паспорт — не таможенный документ, а итог расчёта на дату, который можно
отправить клиенту или поставщику. Все суммы — из CalcOut (тот же сборщик,
что и проект ДТ), ставки и меры — из справки по коду (calc.lookup).
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, SimpleDocTemplate, Spacer

from feap.calc.schemas import CalcOut, CalcParams, ClassificationNote
from feap.dt.exporters.pdf import (
    FONT,
    _money,
    _p,
    _styles,
    _table,
    ensure_fonts,
)
from feap.dt.exporters.xlsx import PAYMENT_KIND_RU
from feap.schemas.api import CodeLookupResponse

_MUTED = colors.Color(0.35, 0.35, 0.35)
_WARN_BG = colors.Color(1.0, 0.95, 0.8)
_TOTAL_BG = colors.Color(0.93, 0.93, 0.9)

#: Уровни дерева tnved_tree (0=раздел … 4=подсубпозиция).
LEVEL_RU = {0: "Раздел", 1: "Группа", 2: "Позиция", 3: "Субпозиция", 4: "Подсубпозиция"}
_SEV_RU = {"error": "Ошибка", "warning": "Внимание", "info": "Инфо"}
_DECISION_RU = {
    "applies": "уплачивается",
    "not_applicable": "товар не подпадает",
    "exempt": "освобождение",
}
_KIND_RU = {
    "antidumping": "Антидемпинговая пошлина",
    "special": "Специальная пошлина",
    "countervailing": "Компенсационная пошлина",
}
_REASONING_MAX = 700


def _date_ru(day: dt.date) -> str:
    return day.strftime("%d.%m.%Y")


def _pct(raw: str) -> str:
    return f"{raw.replace('.', ',')}%"


def _qty(value: Decimal) -> str:
    """1000.000 → «1 000», 12.5 → «12,5»."""
    text = format(value.normalize(), "f")
    whole, _, frac = text.partition(".")
    whole = f"{int(whole):,}".replace(",", " ")
    return f"{whole},{frac}" if frac else whole


def _remedy_rates(rates: list) -> str:
    nums = sorted({Decimal(r.rate_pct) for r in rates})
    if not nums:
        return "ставка не разобрана"
    if len(nums) == 1:
        return _pct(format(nums[0], "f"))
    return f"{_pct(format(nums[0], 'f'))[:-1]}–{_pct(format(nums[-1], 'f'))}"


def _on_page(canvas, doc) -> None:  # noqa: ANN001 — сигнатура reportlab
    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawString(
        15 * mm, 8 * mm,
        "Паспорт товара · FEAP · предварительный расчёт, не таможенный документ",
    )
    canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Лист {canvas.getPageNumber()}")
    canvas.restoreState()


def render_passport_pdf(
    *,
    title: str,
    sku: str | None,
    lookup: CodeLookupResponse,
    tree_chain: list[tuple[int, str, str]],
    classification: ClassificationNote | None,
    params: CalcParams | None,
    calc: CalcOut | None,
    on_date: dt.date,
) -> bytes:
    """tree_chain — [(level, code_formatted, name)] от раздела до листа."""
    ensure_fonts()
    st = _styles()
    width = A4[0] - 30 * mm
    story: list = []

    story.append(_p("Паспорт товара", st["title"]))
    meta = [f"ставки и курсы на {_date_ru(on_date)}"]
    if sku:
        meta.insert(0, f"SKU {sku}")
    meta.append(f"сформирован {_date_ru(dt.date.today())}")
    story.append(_p(" · ".join(meta), st["meta"]))
    story.append(_p(title, st["h2"]))

    # ── Код и обоснование ───────────────────────────────────────────────────
    story.append(_p(f"Код ТН ВЭД ЕАЭС {lookup.code_formatted}", st["h2"]))
    rows = [[_p("Уровень", st["cell_b"]), _p("Код", st["cell_b"]), _p("Наименование", st["cell_b"])]]
    for level, code, name in tree_chain:
        is_leaf = code.replace(" ", "") == lookup.code
        style = st["cell_b"] if is_leaf else st["cell"]
        rows.append([_p("Код" if is_leaf else LEVEL_RU.get(level, "—"), style),
                     _p(code, style), _p(name, style)])
    story.append(_table(rows, [28 * mm, 32 * mm, width - 60 * mm]))
    if classification is not None:
        if classification.source == "cascade":
            conf = (f", уверенность {classification.confidence:.2f}".replace(".", ",")
                    if classification.confidence is not None else "")
            story.append(Spacer(1, 2 * mm))
            story.append(_p(f"Обоснование: каскад классификации по ОПИ{conf}", st["cell_b"]))
            for step in classification.steps:
                reasoning = (step.reasoning or "").strip()
                if len(reasoning) > _REASONING_MAX:
                    reasoning = reasoning[:_REASONING_MAX].rstrip() + "…"
                head = f"{step.code} — {step.name}" if step.name else step.code
                story.append(_p(f"{head}. {reasoning}" if reasoning else head, st["cell"]))
        elif classification.source == "manual":
            story.append(Spacer(1, 2 * mm))
            story.append(_p("Код указан пользователем, обоснование не приложено.", st["cell"]))

    # ── Ставки и меры ───────────────────────────────────────────────────────
    story.append(_p("Ставки и меры", st["h2"]))
    vat = lookup.vat.rate + (f" · льгота: {lookup.vat.exemption.legal_ref}" if lookup.vat.exemption else "")
    excise = (f"{lookup.excise.rate} {lookup.excise.rate_unit or ''}".strip()
              if lookup.excise.applicable else "нет")
    rates_rows = [
        [_p("Пошлина ЕТТ", st["cell"]), _p(lookup.duty_rate or "—", st["cell_b"])],
        [_p("НДС", st["cell"]), _p(vat, st["cell_b"])],
        [_p("Акциз", st["cell"]), _p(excise, st["cell"])],
        [_p("Сбор за таможенные операции", st["cell"]),
         _p(lookup.customs_fee or "не рассчитывается", st["cell"])],
    ]
    story.append(_table(rates_rows, [55 * mm, width - 55 * mm], header=False))

    decisions = (params.remedy_decisions if params else {}) or {}
    origin = params.origin_country if params else None
    if lookup.trade_remedies:
        story.append(Spacer(1, 2 * mm))
        rm = [[_p("Мера защиты рынка ЕАЭС", st["cell_b"]), _p("Страны", st["cell_b"]),
               _p("Ставка", st["cell_b"]), _p("Решение", st["cell_b"])]]
        for m in lookup.trade_remedies:
            decision = decisions.get(m.measure_no)
            text = _DECISION_RU.get(decision.decision, decision.decision) if decision else "может применяться"
            until = f", до {_date_ru(dt.date.fromisoformat(m.valid_to))}" if m.valid_to else ""
            rm.append([
                _p(f"{m.measure_no} · {_KIND_RU.get(m.kind, m.kind)} «{m.title}»{until}", st["cell"]),
                _p(", ".join(m.countries), st["cell"]),
                _p(_remedy_rates(m.rates), st["cell_b"]),
                _p(text, st["cell"]),
            ])
        story.append(_table(rm, [width - 85 * mm, 25 * mm, 25 * mm, 35 * mm],
                            row_bg=[(i, _WARN_BG) for i in range(1, len(rm))]))
        story.append(_p(
            "Мера совпала по коду" + (f" и стране {origin}" if origin else "") +
            ": применяется, только если товар подпадает под её описание (решение ЕЭК).",
            st["cell"],
        ))
    if lookup.vat.conditional:
        story.append(Spacer(1, 2 * mm))
        for c in lookup.vat.conditional:
            story.append(_p(f"Возможна ставка НДС {c.rate} при условии: {c.condition} ({c.legal_ref})",
                            st["cell"]))
    if lookup.ntm and lookup.ntm.measures:
        story.append(Spacer(1, 2 * mm))
        story.append(_p("Нетарифные меры (проверьте применимость к товару):", st["cell_b"]))
        for nm in lookup.ntm.measures[:10]:
            story.append(_p(f"• {nm.measure_type}: {nm.name}", st["cell"]))

    # ── Расчёт платежей и себестоимость ─────────────────────────────────────
    if calc is not None and params is not None:
        story.append(_p("Расчёт для партии", st["h2"]))
        lot = [
            ["Стоимость по счёту", f"{_money(params.price)} {params.currency}, {params.incoterms}"],
            ["Курс НБКР", f"{_qty(calc.fx_rate)} сом за 1 {params.currency}" if calc.fx_rate else "—"],
            ["Страна происхождения", params.origin_country or "не указана"],
        ]
        if params.manufacturer:
            lot.append(["Производитель", params.manufacturer])
        if params.qty:
            lot.append(["Количество", f"{_qty(params.qty)} {params.qty_unit or ''}".strip()])
        lot.append(["Таможенная стоимость", f"{_money(calc.customs_value)} сом"])
        story.append(_table([[_p(a, st["cell"]), _p(b, st["cell"])] for a, b in lot],
                            [55 * mm, width - 55 * mm], header=False))

        story.append(Spacer(1, 2 * mm))
        pay = [[_p("Платёж", st["cell_b"]), _p("Код", st["cell_b"]), _p("Основа, сом", st["cell_b"]),
                _p("Ставка", st["cell_b"]), _p("Сумма, сом", st["cell_b"])]]
        for line in calc.payments:
            name = PAYMENT_KIND_RU.get(line.kind, line.kind)
            if line.ref and line.kind not in ("duty", "vat", "excise"):
                name = f"{name} ({line.ref})"
            amount = _money(line.amount) if line.amount is not None else "не рассчитано"
            # У сбора ставка — целое правило с мин./макс.; оно уже в «Ставках и мерах».
            rate = line.rate.split(" от ")[0] if line.kind == "fee" else line.rate
            pay.append([_p(name, st["cell"]), _p(line.code, st["cell"]), _p(_money(line.basis), st["cell"]),
                        _p(rate, st["cell"]), _p(amount, st["cell_b"])])
        if calc.payments_total is not None:
            pay.append([_p("Итого платежей", st["cell_b"]), "", "", "",
                        _p(_money(calc.payments_total), st["cell_b"])])
        story.append(_table(pay, [width - 115 * mm, 15 * mm, 35 * mm, 30 * mm, 35 * mm],
                            row_bg=[(len(pay) - 1, _TOTAL_BG)] if calc.payments_total is not None else None))

        if calc.missing_parameters:
            story.append(Spacer(1, 2 * mm))
            story.append(_p("Расчёт не завершён — не хватает данных:", st["cell_b"]))
            for m in calc.missing_parameters:
                story.append(_p(f"• {m}", st["cell"]))

        lc = calc.landed_cost
        if lc is not None:
            story.append(_p("Себестоимость ввоза", st["h2"]))
            rows = [[_p("Статья", st["cell_b"]), _p("В валюте", st["cell_b"]), _p("Сумма, сом", st["cell_b"])]]
            for ln in lc.lines:
                src = f"{_money(ln.source_amount)} {ln.source_currency}" if ln.source_currency else ""
                rows.append([_p(ln.label, st["cell"]), _p(src or "—", st["cell"]),
                             _p(_money(ln.amount), st["cell"])])
            totals = [
                ("Итого с НДС", lc.total),
                ("НДС (плательщику НДС — к зачёту)", lc.vat),
                ("Итого без НДС", lc.total_without_vat),
            ]
            first_total = len(rows)
            for label, value in totals:
                rows.append([_p(label, st["cell_b"]), "", _p(_money(value), st["cell_b"])])
            if lc.per_unit is not None:
                unit = f" за 1 {lc.qty_unit}" if lc.qty_unit else " за единицу"
                rows.append([_p(f"На единицу{unit}: с НДС / без НДС", st["cell_b"]), "",
                             _p(f"{_money(lc.per_unit)} / {_money(lc.per_unit_without_vat)}", st["cell_b"])])
            story.append(_table(rows, [width - 80 * mm, 40 * mm, 40 * mm],
                                row_bg=[(i, _TOTAL_BG) for i in range(first_total, len(rows))]))

        if calc.findings:
            story.append(_p("Проверки", st["h2"]))
            fr = [[_p(_SEV_RU.get(f.severity, f.severity), st["cell_b"]), _p(f.message, st["cell"])]
                  for f in calc.findings]
            story.append(_table(fr, [25 * mm, width - 25 * mm], header=False))

    story.append(Spacer(1, 4 * mm))
    story.append(KeepTogether([_p(
        "Расчёт предварительный и не заменяет таможенную декларацию: окончательные суммы "
        "определяются при декларировании. Источники: Единый таможенный тариф ЕАЭС, реестр мер "
        "защиты внутреннего рынка ЕЭК, курсы НБКР, НК КР (НДС), ПП КР № 79 (сбор). "
        "Подпадание товара под меры защиты рынка и нетарифные меры подтверждает декларант.",
        st["meta"],
    )]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Паспорт товара {lookup.code_formatted}", author="FEAP",
    )
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
