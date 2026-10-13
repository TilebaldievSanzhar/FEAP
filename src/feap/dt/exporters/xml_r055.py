"""XML-экспорт проекта ДТ (Решение ЕЭК № 75, идентификатор структуры R.055).

Честно про статус формата: официальный альбом структур R.055 и его XSD в
проект не входят (правки Решения 75 выходят ежегодно — dt_draft_spec.md §12.4).
Экспортёр генерирует ПРЕДВАРИТЕЛЬНУЮ структуру: элементы соответствуют графам
ДТ 1:1, версия структуры конфигурируема (DT_XML_STRUCTURE_VERSION) и пишется
в атрибут structureVersion; финальный маппинг на элементы альбома
подтверждается пилотом загрузки в АРМ «Брокер»/KEDEN (вопрос №1 кастдева).

XSD-валидация — опциональный хук: DT_XML_XSD_PATH + пакет `xmlschema`
(в обязательные зависимости не входит). Без него документ гарантированно
well-formed (ElementTree), содержимое проверяет ФЛК до экспорта.

Деньги приходят строками из graphs (Decimal сериализован при сборке) —
экспортёр их не трогает и не пересчитывает.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from feap.config import settings

XMLNS = "urn:feap:dt:r055-draft"

DISCLAIMER = (
    "Проект документа. Подготовлен программным средством, требует проверки "
    "декларантом. Не является поданной декларацией."
)


def _sub(parent: ET.Element, tag: str, text: object = None, **attrs: object) -> ET.Element:
    """Дочерний элемент; None-атрибуты опускаются. Возвращает элемент."""
    el = ET.SubElement(
        parent, tag,
        {k: str(v) for k, v in attrs.items() if v is not None},
    )
    if text is not None:
        el.text = str(text)
    return el


def _opt(parent: ET.Element, tag: str, text: object = None, **attrs: object) -> None:
    """Элемент добавляется только если есть текст или хотя бы один атрибут."""
    attrs = {k: v for k, v in attrs.items() if v is not None}
    if text is None and not attrs:
        return
    _sub(parent, tag, text, **attrs)


def _party(parent: ET.Element, tag: str, party: dict | None) -> None:
    if not party:
        return
    el = _sub(parent, tag)
    _opt(el, "Name", party.get("name"))
    _opt(el, "Country", party.get("country"))
    _opt(el, "Address", party.get("address"))
    _opt(el, "INN", party.get("inn"))
    if len(el) == 0:
        parent.remove(el)


def _payment(parent: ET.Element, line: dict) -> None:
    el = _sub(
        parent, "Payment",
        kind=line.get("kind"), code=line.get("code"),
        basis=line.get("basis"), rate=line.get("rate"),
        amount=line.get("amount"), ref=line.get("ref"),
    )
    for missing in line.get("missing") or []:
        _sub(el, "Missing", missing)
    _opt(el, "Note", line.get("note"))


def _header(root: ET.Element, header: dict) -> None:
    el = _sub(root, "Header")
    g01 = header.get("g01") or {}
    _opt(el, "G01", None, direction=g01.get("direction"),
         procedure=g01.get("procedure"), form=g01.get("form"))
    _party(el, "G02", header.get("g02"))
    _opt(el, "G03", None, sheets=(header.get("g03") or {}).get("sheets"))
    _opt(el, "G05", header.get("g05"))
    _opt(el, "G06", header.get("g06"))
    _opt(el, "G07", header.get("g07"))
    _party(el, "G08", header.get("g08"))
    _party(el, "G09", header.get("g09"))
    _opt(el, "G11", header.get("g11"))
    _opt(el, "G12", header.get("g12"))
    _party(el, "G14", header.get("g14"))
    _opt(el, "G15", header.get("g15"))
    _opt(el, "G17", header.get("g17"))
    _opt(el, "G18", header.get("g18"))
    _opt(el, "G19", header.get("g19"))
    g20 = header.get("g20") or {}
    _opt(el, "G20", None, incoterms=g20.get("incoterms"), place=g20.get("place"))
    _opt(el, "G21", header.get("g21"))
    g22 = header.get("g22") or {}
    _opt(el, "G22", None, currency=g22.get("currency"), amount=g22.get("amount"))
    g23 = header.get("g23") or {}
    _opt(el, "G23", None, rate=g23.get("rate"), date=g23.get("date"))
    _opt(el, "G24", header.get("g24"))
    _opt(el, "G25", header.get("g25"))
    g37 = header.get("g37") or {}
    _opt(el, "G37", None, requested=g37.get("requested"),
         previous=g37.get("previous"), feature=g37.get("feature"))


def _item(goods: ET.Element, item: dict) -> None:
    el = _sub(goods, "Item", number=item.get("g32"))
    g31 = item.get("g31") or {}
    g31_el = _sub(el, "G31")
    _opt(g31_el, "Description", g31.get("description"))
    _opt(g31_el, "Qty", g31.get("qty"), unit=g31.get("qty_unit"))
    _opt(g31_el, "Packages", g31.get("packages"))
    _opt(g31_el, "Sku", g31.get("sku"))
    _opt(g31_el, "Manufacturer", g31.get("manufacturer"))
    _opt(el, "G33", item.get("g33"))
    _opt(el, "G34", item.get("g34"))
    _opt(el, "G35", item.get("g35"))
    _opt(el, "G36", item.get("g36"))
    _opt(el, "G38", item.get("g38"))
    g41 = item.get("g41") or {}
    _opt(el, "G41", g41.get("qty"), unit=g41.get("unit"),
         requiredUnit=g41.get("required_unit"))
    _opt(el, "G42", item.get("g42"))
    _opt(el, "G43", item.get("g43"))
    docs44 = item.get("g44") or []
    if docs44:
        g44_el = _sub(el, "G44")
        for doc in docs44:
            _sub(g44_el, "Doc",
                 code=doc.get("code"), kind=doc.get("kind"),
                 number=doc.get("number"), date=doc.get("date"),
                 permitStatus=doc.get("permit_status"))
    _opt(el, "G45", item.get("g45"))
    _opt(el, "G46", item.get("g46"))
    payments = item.get("g47") or []
    if payments:
        g47_el = _sub(el, "G47")
        for line in payments:
            _payment(g47_el, line)


def render_xml(graphs: dict[str, Any], *, structure_version: str | None = None) -> bytes:
    """graphs драфта → XML (UTF-8, с декларацией)."""
    meta = graphs.get("meta") or {}
    header = graphs.get("header") or {}
    items = graphs.get("items") or []
    totals = graphs.get("totals") or {}

    root = ET.Element("DTDraft", {
        "xmlns": XMLNS,
        "structureVersion": structure_version or settings.DT_XML_STRUCTURE_VERSION,
        "procedure": str(meta.get("procedure") or ""),
        "rateDate": str(meta.get("rate_date") or ""),
        "generatedAt": str(meta.get("generated_at") or ""),
    })
    _sub(root, "Disclaimer", DISCLAIMER)
    _header(root, header)

    goods = _sub(root, "Goods", count=len(items))
    for item in items:
        _item(goods, item)

    totals_el = _sub(
        root, "Totals",
        invoiceTotal=totals.get("invoice_total"),
        customsValue=totals.get("g12"),
        additions=totals.get("additions_total"),
        sheets=totals.get("g03_sheets"),
    )
    payments_el = _sub(totals_el, "Payments")
    for kind, amount in (totals.get("payments") or {}).items():
        _sub(payments_el, "Total", amount, kind=kind)
    fee_line = totals.get("fee_line")
    if fee_line:
        _payment(totals_el, fee_line)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def validate_against_xsd(xml_bytes: bytes) -> list[str] | None:
    """Валидация по официальному XSD, если он настроен.

    None — валидация недоступна (XSD не задан или пакет xmlschema не
    установлен); [] — документ валиден; иначе — список ошибок.
    """
    xsd_path = (settings.DT_XML_XSD_PATH or "").strip()
    if not xsd_path:
        return None
    try:
        import xmlschema  # optional dependency
    except ImportError:
        return [
            "DT_XML_XSD_PATH задан, но пакет xmlschema не установлен "
            "(pip install xmlschema) — валидация невозможна"
        ]
    try:
        schema = xmlschema.XMLSchema(xsd_path)
    except Exception as exc:  # noqa: BLE001 — битый/недоступный XSD
        return [f"Не удалось загрузить XSD {xsd_path}: {exc}"]
    errors = [
        f"{err.reason or err}"
        for err in schema.iter_errors(xml_bytes.decode("utf-8"))
    ]
    return errors
