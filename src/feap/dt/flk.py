"""ФЛК — форматно-логический контроль проекта ДТ.

Движок правил поверх собранных graphs + исходного досье + RefContext
(протокол справочных данных — БД-независимый, в тестах подменяется фейком).

Severity: error блокирует статус ready; warning/info — нет.
Коды правил — docs/dt_draft_spec.md §7, слой 1 полностью (Фаза 3):
F-01..F-15 + W-xx. Тарифные меры (миграция 012, product_vision_v3.md §8):
F-16 (ставка ЗСТ не применима), F-17 (неприменимая льгота НДС), W-FTA, I-EAEU,
W-REMEDY-EXEMPT, I-REMEDY-NA, W-REMEDY-VAT, W-VAT-COND, I-VAT-COND — читают
items[i].tariff (след сборки). Отступления от спеки:
- F-14 — warning, а не error: спека требует документ для «applies
  (verified)» меры НТР, но верификация применимости (/ntm/verify — LLM)
  на досье не сохраняется, поэтому предикат срабатывает по may_apply;
- F-09 по странам/валютам — формат уже enforce'ится схемой (pydantic
  pattern) и F-08; классификаторы стран/валют сознательно не сидируются
  (неполный список дал бы ложные ошибки).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from feap.dt.customs_value import FREIGHT_EXPECTED
from feap.dt.schemas import NO_PREFERENCE, REMEDY_KINDS, Finding, ShipmentIn

_INN_KG_RE = re.compile(r"^\d{14}$")
_TOLERANCE_PCT = Decimal("0.5")   # F-04: допуск расхождения счёта, %
_ADVALOREM_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*%$")   # F-10: «10%», «12 %»
_MONEY_EPS = Decimal("0.01")      # F-10: допуск на квантование одной строки

#: Графа 36: значения «без льгот» (нули / буква О кириллицей и латиницей).
_NO_PREFERENCE = NO_PREFERENCE

_PREFERENTIAL_REGIMES = {"fta", "fta_interim"}
_REMEDY_KIND_RU = {
    "antidumping": "антидемпинговой", "special": "специальной", "countervailing": "компенсационной",
}

#: Статусы TULPAR-проверки, требующие внимания (W-TULPAR / спека R-05).
_PERMIT_BAD_STATUSES = {
    "not_found", "status_mismatch", "code_mismatch", "format_invalid",
}


@dataclass(frozen=True)
class CodeInfo:
    exists: bool
    is_leaf: bool
    unit_secondary: str | None = None
    duty_raw: str | None = None


class RefContext(Protocol):
    """Справочные данные, нужные ФЛК. Реализации: refdata.RefSnapshot / фейки в тестах."""

    def code_info(self, code10: str) -> CodeInfo | None: ...

    def classifier_codes(self, classifier: str) -> set[str] | None:
        """Множество кодов классификатора Решения 378; None = не загружен."""
        ...

    def has_ntm_measures(self, code10: str) -> bool:
        """Зарегистрированы ли меры НТР для кода (may_apply, без верификации)."""
        ...


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def validate_draft(
    graphs: dict,
    shipment: ShipmentIn,
    ctx: RefContext,
) -> list[Finding]:
    findings: list[Finding] = []
    header: dict = graphs.get("header", {})
    items: list[dict] = graphs.get("items", [])
    totals: dict = graphs.get("totals", {})

    def add(rule: str, severity: str, graph: str | None, message: str, **details: object) -> None:
        findings.append(Finding(
            rule=rule, severity=severity, graph=graph, message=message,
            details={k: str(v) for k, v in details.items()},
        ))

    # ── F-01: обязательные графы для ИМ 40 ─────────────────────────────────
    required = {
        "g01": "тип декларации", "g02": "отправитель", "g05": "всего товаров",
        "g08": "получатель", "g12": "общая таможенная стоимость",
        "g14": "декларант", "g20": "условия поставки", "g22": "валюта и сумма счёта",
        "g23": "курс валюты",
    }
    for g, label in required.items():
        val = header.get(g)
        empty = val is None or (isinstance(val, dict) and not any(v for v in val.values()))
        if empty:
            add("F-01", "error", g, f"Не заполнена обязательная графа {g} ({label})")
    if not items:
        add("F-01", "error", None, "В декларации нет ни одного товара")

    # ── F-02: счётчики листов и товаров ────────────────────────────────────
    n = len(items)
    if header.get("g05") != n:
        add("F-02", "error", "g05", f"Графа 5 ({header.get('g05')}) не равна числу позиций ({n})")
    sheets_expected = totals.get("g03_sheets")
    g03 = (header.get("g03") or {}).get("sheets")
    if sheets_expected is not None and g03 != sheets_expected:
        add("F-02", "error", "g03", f"Число листов {g03} не соответствует расчётному {sheets_expected}")
    for i, item in enumerate(items):
        if item.get("g32") != i + 1:
            add("F-02", "error", f"items[{i}].g32", "Нарушена сквозная нумерация товаров")

    # ── F-03: коды существуют и являются листьями ──────────────────────────
    for i, item in enumerate(items):
        code = item.get("g33")
        ref = f"items[{i}].g33"
        if not code:
            add("F-03", "error", ref, f"Товар {i + 1}: не указан код ТН ВЭД (графа 33)")
            continue
        info = ctx.code_info(str(code))
        if info is None or not info.exists:
            add("F-03", "error", ref, f"Товар {i + 1}: код {code} не найден в номенклатуре")
        elif not info.is_leaf:
            add("F-03", "error", ref, f"Товар {i + 1}: код {code} не является конечным (декларируемым)")

    # ── F-04: сумма цен позиций = итог счёта (графа 22) ────────────────────
    g22_amount = _dec((header.get("g22") or {}).get("amount"))
    prices_sum = sum((p for p in (_dec(it.get("g42")) for it in items) if p is not None), Decimal(0))
    if g22_amount is not None and items:
        if g22_amount == 0:
            if prices_sum != 0:
                add("F-04", "error", "g22", "Итог счёта равен 0 при ненулевых ценах позиций")
        else:
            diff_pct = abs(prices_sum - g22_amount) / g22_amount * 100
            if diff_pct > _TOLERANCE_PCT:
                add(
                    "F-04", "error", "g22",
                    f"Σ цен позиций ({prices_sum}) расходится с графой 22 ({g22_amount}) "
                    f"на {diff_pct.quantize(Decimal('0.01'))}% (> {_TOLERANCE_PCT}%)",
                )

    # ── F-05: Σ графы 45 = графа 12 ────────────────────────────────────────
    g12 = _dec(header.get("g12"))
    cv_sum = sum((v for v in (_dec(it.get("g45")) for it in items) if v is not None), Decimal(0))
    if g12 is not None and cv_sum != g12:
        add("F-05", "error", "g12", f"Σ графы 45 ({cv_sum}) не равна графе 12 ({g12})")

    # ── F-06: вес нетто ≤ брутто ───────────────────────────────────────────
    for i, item in enumerate(items):
        net, gross = _dec(item.get("g38")), _dec(item.get("g35"))
        if net is not None and gross is not None and net > gross:
            add("F-06", "error", f"items[{i}].g38",
                f"Товар {i + 1}: вес нетто ({net}) больше брутто ({gross})")

    # ── F-07: доп. единица обязательна, если код её требует ────────────────
    for i, item in enumerate(items):
        code = item.get("g33")
        info = ctx.code_info(str(code)) if code else None
        required_unit = (item.get("g41") or {}).get("required_unit") or (
            info.unit_secondary if info else None
        )
        has_qty = _dec((item.get("g41") or {}).get("qty")) is not None
        if required_unit and not has_qty:
            add("F-07", "error", f"items[{i}].g41",
                f"Товар {i + 1}: код требует доп. единицу «{required_unit}» (графа 41)")

    # ── F-08: валюта и курс ────────────────────────────────────────────────
    currency = (header.get("g22") or {}).get("currency")
    if not currency or not re.match(r"^[A-Z]{3}$", str(currency)):
        add("F-08", "error", "g22", f"Некорректный код валюты: {currency!r}")
    rate = _dec((header.get("g23") or {}).get("rate"))
    if rate is None or rate <= 0:
        add("F-08", "error", "g23", "Курс валюты отсутствует или неположителен")

    # ── F-11: ИНН получателя (формат КР — 14 цифр) ─────────────────────────
    buyer_inn = (header.get("g08") or {}).get("inn")
    if buyer_inn and not _INN_KG_RE.match(str(buyer_inn)):
        add("F-11", "warning", "g08",
            f"ИНН получателя {buyer_inn!r} не соответствует формату КР (14 цифр)")

    # ── W-31: слишком короткое описание товара ─────────────────────────────
    for i, item in enumerate(items):
        desc = ((item.get("g31") or {}).get("description") or "").strip()
        if desc and len(desc) < 30:
            add("W-31", "warning", f"items[{i}].g31",
                f"Товар {i + 1}: описание короче 30 символов — инспектору может не хватить "
                "для подтверждения кода (материал, назначение, характеристики)")

    # ── W-FREIGHT: базис требует фрахта, а он нулевой ──────────────────────
    if shipment.incoterms in FREIGHT_EXPECTED and shipment.freight_to_border == 0:
        add("W-FREIGHT", "warning", "g20",
            f"Базис {shipment.incoterms} предполагает добавление фрахта до границы ЕАЭС, "
            "но фрахт не указан — таможенная стоимость может быть занижена (риск КТС)")

    # ── W-FEE: сбор за таможенные операции не рассчитан ────────────────────
    if not (totals.get("payments") or {}).get("fee"):
        add("W-FEE", "info", None,
            "Сбор за таможенные операции не рассчитан (нет таможенной стоимости или "
            "расчёт сбора отключён настройкой DT_CUSTOMS_FEE_MODE) — по п. 38 ПП КР № 79 "
            "это 0,4% от таможенной стоимости, не менее 5 и не более 2500 РП")

    # ── F-09: коды по классификаторам Решения 378 ──────────────────────────
    checks: list[tuple[str, str, object]] = [
        ("g20", "delivery_terms", (header.get("g20") or {}).get("incoterms")),
        ("g24", "deal_nature", header.get("g24")),
        ("g25", "transport_types", header.get("g25")),
        ("g37", "customs_procedures", (header.get("g37") or {}).get("requested")),
    ]
    for i, item in enumerate(items):
        for doc in item.get("g44") or []:
            checks.append((f"items[{i}].g44", "doc_kinds", doc.get("code")))
        for line in item.get("g47") or []:
            checks.append((f"items[{i}].g47", "payment_kinds", line.get("code")))

    skipped_classifiers: set[str] = set()
    for graph_ref, classifier, value in checks:
        if not value:
            continue
        codes = ctx.classifier_codes(classifier)
        if codes is None:
            skipped_classifiers.add(classifier)
            continue
        if str(value) not in codes:
            add("F-09", "error", graph_ref,
                f"Код «{value}» отсутствует в классификаторе {classifier} "
                "(Решение 378)")
    if skipped_classifiers:
        add("F-09", "info", None,
            "Классификаторы не загружены, проверка кодов пропущена: "
            f"{', '.join(sorted(skipped_classifiers))} "
            "(наполнение: python scripts/seed/classifiers.py)")

    # ── F-10: арифметика графы 47 (самопроверка расчёта) ────────────────────
    vat_includes_remedies = bool((graphs.get("meta") or {}).get("vat_base_includes_remedies"))
    sums: dict[str, Decimal] = {}
    for i, item in enumerate(items):
        g47 = item.get("g47") or []
        for line in g47:
            kind = line.get("kind")
            amount = _dec(line.get("amount"))
            if amount is not None:
                sums[kind] = sums.get(kind, Decimal(0)) + amount
            basis = _dec(line.get("basis"))
            match = _ADVALOREM_RE.match(str(line.get("rate") or ""))
            if amount is None or basis is None or not match:
                continue   # специфические/комбинированные ставки не пересчитываем
            pct = Decimal(match.group(1).replace(",", "."))
            expected = basis * pct / 100
            if abs(amount - expected) > _MONEY_EPS:
                add("F-10", "error", f"items[{i}].g47",
                    f"Товар {i + 1}: {kind} {line.get('rate')} от {basis} даёт "
                    f"{expected.quantize(_MONEY_EPS)}, в графе — {amount}")
        # база НДС = ТС + пошлина + акциз (+ пошлины защиты рынка по настройке)
        lines = {line.get("kind"): line for line in g47}
        vat, duty_line = lines.get("vat"), lines.get("duty")
        excise_line = lines.get("excise")
        vat_basis = _dec(vat.get("basis")) if vat else None
        cv = _dec(item.get("g45"))
        if vat_basis is not None and cv is not None:
            duty_amount = _dec(duty_line.get("amount")) if duty_line else Decimal(0)
            excise_amount = _dec(excise_line.get("amount")) if excise_line else Decimal(0)
            remedy_amount = Decimal(0)
            if vat_includes_remedies:
                for line in g47:
                    if line.get("kind") in REMEDY_KINDS:
                        remedy_amount += _dec(line.get("amount")) or Decimal(0)
            if duty_amount is not None and excise_amount is not None:
                expected_base = cv + duty_amount + excise_amount + remedy_amount
                if abs(vat_basis - expected_base) > _MONEY_EPS:
                    label = "ТС + пошлина + акциз" + (
                        " + пошлины защиты рынка" if vat_includes_remedies else "")
                    add("F-10", "error", f"items[{i}].g47",
                        f"Товар {i + 1}: база НДС ({vat_basis}) ≠ {label} "
                        f"({expected_base})")
    for kind, total in sums.items():
        stated = _dec((totals.get("payments") or {}).get(kind))
        if stated is not None and abs(total - stated) > _MONEY_EPS * (len(items) + 1):
            add("F-10", "error", None,
                f"Σ платежей «{kind}» по товарам ({total}) не сходится с итогом "
                f"({stated})")

    # ── F-13: заявлена преференция (36) → подтверждающий документ в 44 ─────
    for i, item in enumerate(items):
        preference = str(item.get("g36") or "").strip()
        if not preference or preference.upper() in _NO_PREFERENCE:
            continue
        docs44 = item.get("g44") or []
        if not any(d.get("kind") in ("permit", "cert_origin") for d in docs44):
            add("F-13", "error", f"items[{i}].g36",
                f"Товар {i + 1}: заявлена преференция «{preference}», но в графе 44 "
                "нет подтверждающего документа (сертификат происхождения/разрешение)")

    # ── F-14: меры НТР по коду → разрешительный документ в 44 ──────────────
    # Warning (не error, как в спеке): применимость меры к конкретному товару
    # не верифицирована (see module docstring).
    for i, item in enumerate(items):
        code = item.get("g33")
        if not code or not ctx.has_ntm_measures(str(code)):
            continue
        docs44 = item.get("g44") or []
        if not any(d.get("kind") == "permit" for d in docs44):
            add("F-14", "warning", f"items[{i}].g44",
                f"Товар {i + 1}: для кода {code} зарегистрированы меры нетарифного "
                "регулирования — может требоваться разрешительный документ в графе 44 "
                f"(проверьте GET /ntm/{code} и POST /ntm/verify)")

    # ── Тарифные меры (items[i].tariff — след сборки, см. assembler) ─────────
    _tariff_rules(items, add)

    # ── F-15: даты документов ────────────────────────────────────────────────
    reference_date = _reference_date(graphs)
    docs44_all = (items[0].get("g44") if items else None) or []
    contract_dates, invoice_dates = [], []
    for doc in docs44_all:
        doc_date = _date(doc.get("date"))
        if doc_date is None:
            continue
        label = f"{doc.get('kind')} №{doc.get('number') or '—'}"
        if reference_date and doc_date > reference_date:
            add("F-15", "warning", None,
                f"Дата документа {label} ({doc_date.isoformat()}) позже даты досье "
                f"({reference_date.isoformat()})")
        if doc.get("kind") == "contract":
            contract_dates.append((doc_date, label))
        elif doc.get("kind") == "invoice":
            invoice_dates.append((doc_date, label))
    if contract_dates and invoice_dates:
        latest_contract = max(contract_dates)
        earliest_invoice = min(invoice_dates)
        if latest_contract[0] > earliest_invoice[0]:
            add("F-15", "warning", None,
                f"Контракт ({latest_contract[1]}, {latest_contract[0].isoformat()}) "
                f"датирован позже инвойса ({earliest_invoice[1]}, "
                f"{earliest_invoice[0].isoformat()}) — проверьте комплект документов")

    # ── W-TULPAR: разрешительный не подтверждён реестром (спека R-05) ───────
    for doc in docs44_all:
        status = doc.get("permit_status")
        if status in _PERMIT_BAD_STATUSES:
            add("W-TULPAR", "warning", None,
                f"Документ {doc.get('kind')} №{doc.get('number') or '—'}: "
                f"проверка по реестрам TULPAR дала статус «{status}» — "
                "документ требует ручной проверки")

    return findings


def _tariff_rules(items: list[dict], add) -> None:  # noqa: C901 — плоский перечень правил
    """F-16/F-17 и предупреждения по ЗСТ, мерам защиты рынка и условному НДС."""
    for i, item in enumerate(items):
        tariff = item.get("tariff") or {}
        n = i + 1
        regime = tariff.get("origin_regime") or {}
        docs44 = item.get("g44") or []
        has_origin_doc = any(d.get("kind") in ("cert_origin", "permit") for d in docs44)

        # F-16: ставка ЗСТ заявлена, но не применена — объясняем почему.
        pref_rate = tariff.get("preferential_duty_rate")
        if pref_rate and not tariff.get("preferential_rate_applied"):
            preference = str(item.get("g36") or "").strip().upper()
            if regime.get("regime") not in _PREFERENTIAL_REGIMES:
                reason = (f"для страны происхождения {item.get('g34') or '—'} нет действующего "
                          "соглашения о свободной торговле на дату расчёта")
            elif not preference or preference in _NO_PREFERENCE:
                reason = "в графе 36 не указан код преференции"
            else:
                reason = "условия применения не выполнены"
            add("F-16", "error", f"items[{i}].g36",
                f"Товар {n}: заявлена ставка по ЗСТ «{pref_rate}», но она не применена — "
                f"{reason}. Пошлина рассчитана по ставке ЕТТ")

        # W-FTA: у страны есть действующий преференциальный режим, ставка ЕТТ.
        if (regime.get("regime") in _PREFERENTIAL_REGIMES
                and not tariff.get("preferential_rate_applied") and not pref_rate):
            proof = regime.get("origin_proof") or "сертификат о происхождении"
            add("W-FTA", "info", f"items[{i}].g36",
                f"Товар {n}: страна происхождения {regime.get('country')} — "
                f"{regime.get('agreement')}. При наличии документа о происхождении ({proof}) "
                "возможна преференциальная ставка по графику соглашения; сейчас применена "
                "ставка ЕТТ")

        # I-EAEU: происхождение из государства — члена ЕАЭС.
        if regime.get("regime") == "eaeu_member":
            add("I-EAEU", "info", f"items[{i}].g34",
                f"Товар {n}: страна происхождения {regime.get('country')} — член ЕАЭС. "
                "Товары ЕАЭС, ввозимые из государств-членов, таможенному декларированию не "
                "подлежат (НДС — через налоговую, форма STI-136); проверьте статус товара")

        # Меры защиты рынка: решения декларанта.
        for remedy in tariff.get("remedies") or []:
            decision = remedy.get("decision")
            label = f"{remedy.get('measure_no')} «{remedy.get('title')}»"
            kind_ru = _REMEDY_KIND_RU.get(remedy.get("kind"), "")
            if decision == "exempt" and not has_origin_doc:
                add("W-REMEDY-EXEMPT", "warning", f"items[{i}].g44",
                    f"Товар {n}: заявлено освобождение от {kind_ru} меры {label}, но в графе 44 "
                    "нет подтверждающего документа (целевое назначение/квота/ценовые "
                    "обязательства/сертификат производителя)")
            elif decision == "not_applicable":
                add("I-REMEDY-NA", "info", f"items[{i}].g33",
                    f"Товар {n}: декларант указал, что товар не подпадает под меру {label} — "
                    "сохраните обоснование (технические характеристики) на случай проверки")

        # W-REMEDY-VAT: пошлины защиты рынка есть — способ учёта в базе НДС не сверен.
        remedy_lines = [
            ln for ln in item.get("g47") or []
            if ln.get("kind") in REMEDY_KINDS and _dec(ln.get("amount"))
        ]
        if remedy_lines:
            add("W-REMEDY-VAT", "warning", f"items[{i}].g47",
                f"Товар {n}: начислена пошлина защиты рынка "
                f"({', '.join(ln.get('ref') or ln.get('kind') for ln in remedy_lines)}). "
                "Ст. 310 НК КР включает в базу НДС «таможенные пошлины и налоги» и прямо не "
                "называет антидемпинговые пошлины (в РК — включаются явно) — сверьте практику "
                "ГТС; учёт в базе задаёт настройка DT_VAT_INCLUDE_REMEDY_DUTIES")

        # Условный НДС: льгота доступна / выбрана / выбрана неприменимая.
        options = tariff.get("vat_options") or []
        chosen = tariff.get("vat_benefit")
        if chosen and not tariff.get("vat_benefit_applied"):
            add("F-17", "error", f"items[{i}].g47",
                f"Товар {n}: выбрана льгота по НДС «{chosen}», но для кода "
                f"{item.get('g33')} на дату расчёта она не действует — применена общая ставка")
        elif chosen:
            opt = next((o for o in options if o.get("source") == chosen), {})
            add("I-VAT-COND", "info", f"items[{i}].g47",
                f"Товар {n}: НДС {opt.get('rate', '0%')} по {opt.get('legal_ref', chosen)} "
                f"при условии «{opt.get('condition', '')}» — приложите документы, "
                "подтверждающие целевое назначение")
        elif options:
            variants = "; ".join(
                f"{o.get('rate')} — {o.get('legal_ref')} ({o.get('condition')})" for o in options
            )
            add("W-VAT-COND", "warning", f"items[{i}].g47",
                f"Товар {n}: по коду возможна льготная ставка НДС при условии целевого "
                f"назначения: {variants}. Применена общая ставка; если условие выполняется — "
                "выберите льготу (vat_benefit)")


def _date(value: object) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _reference_date(graphs: dict) -> _dt.date | None:
    """Дата генерации драфта из meta (self-consistent, тестируемо без now())."""
    return _date((graphs.get("meta") or {}).get("generated_at"))


def draft_status(findings: list[Finding], missing_parameters: list[str]) -> str:
    if missing_parameters:
        return "needs_input"
    if any(f.severity == "error" for f in findings):
        return "has_errors"
    return "ready"
