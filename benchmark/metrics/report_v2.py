"""Markdown report of a v2 scoring run (in Russian, like the team's analysis docs)."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from benchmark.metrics.consequences import STANDARD_LOT
from benchmark.metrics.outcome import ICONS, CaseOutcome, Outcome

LABELS: dict[Outcome, str] = {
    Outcome.CORRECT: 'верно',
    Outcome.SAFE_STOP: 'безопасная остановка',
    Outcome.SILENT_ERROR: 'тихая ошибка',
    Outcome.FAILURE: 'сбой',
}

REASONS: dict[str, str] = {
    'exact': 'точное совпадение', 'acceptable': 'допустимый альтернативный код',
    'flagged_wrong': 'неверный код с флагом «проверить»',
    'question_justified': 'вопрос там, где сведений не хватает',
    'question_unneeded': 'лишний вопрос', 'question': 'вопрос',
    'wrong_unflagged': 'неверный код без флага',
    'not_run': 'не запускался', 'budget': 'бюджет OpenRouter', 'timeout': 'таймаут',
    'error': 'ошибка', 'no_code': 'нет кода в ответе',
}

DIFF_LABELS: dict[str, str] = {
    'same': 'без последствий', 'paperwork': 'другие меры или доп. единица',
    'money': 'меняет платежи', 'unknown': 'не рассчитано',
    'invalid_code': 'кода нет в ЕТТ', 'gold_invalid': 'эталона нет в ЕТТ',
}


def _pct(value: float | None) -> str:
    return '—' if value is None else f'{value * 100:.1f}%'


def _ci(ci: list[float] | None) -> str:
    return '—' if ci is None else f'{ci[0] * 100:.1f}–{ci[1] * 100:.1f}%'


def _kgs(value: str | Decimal | None, *, signed: bool = False) -> str:
    if value is None:
        return '—'
    amount = Decimal(value)
    text = f'{abs(amount):,.2f}'.replace(',', ' ').replace('.', ',')
    if signed and amount:
        return ('−' if amount < 0 else '+') + text
    return text


def _share_line(share: dict) -> str:
    return f"{share['k']} из {share['n']} ({_pct(share['share'])})"


def _changes(record: dict) -> str:
    diff, gold, pred = record['diff'], record['gold'], record['predicted']
    parts = []
    gold_duty, pred_duty = gold['rates'].get('duty'), pred['rates'].get('duty')
    if gold_duty != pred_duty and (gold_duty or pred_duty):
        parts.append(f'пошлина {gold_duty or "—"} → {pred_duty or "—"}')
    if diff['remedies_missed']:
        parts.append('теряется мера ' + ', '.join(diff['remedies_missed']))
    if diff['remedies_extra']:
        parts.append('лишняя мера ' + ', '.join(diff['remedies_extra']))
    if diff['ntm_missed']:
        parts.append(f"теряется НТМ: {len(diff['ntm_missed'])}")
    if diff['ntm_extra']:
        parts.append(f"лишние НТМ: {len(diff['ntm_extra'])}")
    if diff['supp_unit_changed']:
        parts.append(f"доп. единица {gold['supp_unit'] or '—'} → {pred['supp_unit'] or '—'}")
    if pred['missing'] and diff['kind'] == 'unknown':
        parts.append('не хватает: ' + '; '.join(pred['missing'])[:120])
    return '; '.join(parts) or '—'


def _breakdown(title: str, groups: dict[str, dict]) -> list[str]:
    lines = ['', f'### {title}', '', '| | Кейсов | ✅ | 🟡 | 🔴 | ⚫ |', '|---|---:|---:|---:|---:|---:|']
    for name, row in groups.items():
        lines.append(
            f"| {name} | {row['n']} | {row['correct']} | {row['safe_stop']} | "
            f"{row['silent_error']} | {row['failure']} |"
        )
    return lines


def _lot_text() -> str:
    lot = STANDARD_LOT
    price = f'{lot.price:,.0f}'.replace(',', ' ')
    return (f'партия (если у кейса нет своей) — {price} {lot.currency} {lot.incoterms}, '
            f'происхождение {lot.origin_country}, {lot.qty} {lot.qty_unit}, '
            f'{lot.net_kg} кг нетто')


def _refdata_drift(manifest: dict) -> list[str]:
    before = (manifest.get('predictions') or {}).get('reference_data') or {}
    now = manifest.get('reference_data') or {}
    return sorted(t for t in before if t in now and before[t] != now[t])


def render_markdown(summary: dict, outcomes: list[CaseOutcome], priced: dict[str, dict],
                    manifest: dict) -> str:
    n, total = summary['n_scored'], summary['n_cases']
    lines = [
        f"# Бенчмарк v2 — {summary['run_id']}", '',
        f"Кейсы: `{summary['cases_path']}` (sha256 {manifest['cases']['sha256'][:12]}…)  ",
        f"Предсказания: `{summary['predictions_path']}` "
        f"(sha256 {manifest['predictions']['sha256'][:12]}…)  ",
        f"Оценено: {manifest['scored_at'][:10]}", '',
        f'## Светофор: {n} из {total} кейсов с установленным эталоном', '',
        '| Исход | Кейсов | Доля | 95% ДИ (Уилсон) |', '|---|---:|---:|---|',
    ]
    for outcome in Outcome:
        row = summary['outcomes'][outcome.value]
        lines.append(f"| {ICONS[outcome]} {LABELS[outcome]} | {row['k']} | "
                     f"{_pct(row['share'])} | {_ci(row['ci95'])} |")
    if summary['not_scored']:
        lines += ['', f"Не оценивались — эталон в карантине или спорный "
                      f"({len(summary['not_scored'])}): {', '.join(summary['not_scored'])}."]
    lines += ['', 'Причины: ' + '; '.join(
        f'{REASONS.get(reason, reason)} — {count}' for reason, count in summary['reasons'].items()
    ) + '.']

    flags = summary['flags']
    lines += [
        '', '## Флаг «требует проверки»', '',
        f"Неверных кодов: {flags['wrong_codes']}; с флагом из них "
        f"{_share_line(flags['flag_recall_on_wrong'])}. Флаг на верных кодах: "
        f"{_share_line(flags['false_alarm_on_correct'])}. Уверенность модели флагом не считается.",
    ]

    if priced:
        date = next(iter(priced.values()))['date']
        lines += [
            '', '## Цена ошибок', '',
            f'П{_lot_text()[1:]}; ставки, курсы и меры на {date}. Платежи считает '
            f'движок продукта (`calc.run_calc`): пошлина, акциз, НДС, сбор. Меры защиты рынка '
            f'сравниваются как набор — применима ли мера к товару, решает декларант. '
            f'Δ = ответ − эталон: минус — недоплата.', '',
            '| Кейс | Исход | Эталон | Ответ | Последствие | Δ платежей, сом | Что ещё меняется |',
            '|---|---|---|---|---|---:|---|',
        ]
        order = {Outcome.SILENT_ERROR: 0, Outcome.SAFE_STOP: 1}
        rows = sorted(
            (o for o in outcomes if o.case_id in priced),
            key=lambda o: (order.get(o.outcome, 2),
                           -abs(Decimal(priced[o.case_id]['diff']['delta_total'] or 0)), o.case_id),
        )
        for o in rows:
            record = priced[o.case_id]
            lines.append(
                f"| {o.case_id} | {ICONS[o.outcome]} | {o.gold_code} | {o.predicted_code} | "
                f"{DIFF_LABELS.get(record['diff']['kind'], record['diff']['kind'])} | "
                f"{_kgs(record['diff']['delta_total'], signed=True)} | {_changes(record)} |"
            )
        for outcome in (Outcome.SILENT_ERROR, Outcome.SAFE_STOP):
            money = summary['money'][outcome.value]
            if not money['n']:
                continue
            kinds = ', '.join(f'{DIFF_LABELS.get(k, k)} — {v}' for k, v in money['by_kind'].items())
            lines += ['', (
                f"{ICONS[outcome]} {LABELS[outcome].capitalize()}: {money['n']} ({kinds}). "
                f"Недоплата {_kgs(money['underpaid_kgs'])} сом в {money['underpaid_cases']} "
                f"кейсах, переплата {_kgs(money['overpaid_kgs'])} сом в "
                f"{money['overpaid_cases']}; теряется мера защиты рынка — "
                f"{money['remedies_missed_cases']}, нетарифная мера — {money['ntm_missed_cases']}."
            )]

    lines += _breakdown('По сложности', summary['by_difficulty'])
    lines += _breakdown('По статусу эталона', summary['by_verification'])

    verification = Counter(o.verification for o in outcomes)
    limits = []
    if verification.get('unverified'):
        limits.append(
            f"{verification['unverified']} эталонов не перепроверялись по источникам "
            f"(статус unverified): возможны ошибки разметки, как в аудите v32."
        )
    drift = _refdata_drift(manifest)
    if drift:
        limits.append(
            'Предсказания получены на другой версии справочника: ' + ', '.join(drift) +
            '. Ответ модели на текущих данных может отличаться.'
        )
    if priced:
        limits.append(
            'Цена ошибки посчитана на стандартной партии, а не на фактах товара. Если ставка '
            'специфическая в м², л и т.п., доп. единица неизвестна — «не рассчитано».'
        )
        ntm_rows = ((manifest.get('reference_data') or {}).get('ntm_measures') or {}).get('rows')
        if ntm_rows is not None:
            limits.append(
                f'Нетарифные меры — из справочника ({ntm_rows} мер, привязки на уровне групп и '
                f'позиций): совпадение мер не означает совпадения разрешительных документов.'
            )
    if limits:
        lines += ['', '## Ограничения', '', *[f'- {item}' for item in limits]]
    return '\n'.join(lines) + '\n'
