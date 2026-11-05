"""Статусы условий → сигналы S1–S7 → флаг по политике (план, §5.4; предрегистрация, §5).

Собственный статус строки даёт исполнитель (семантика), код (число) или
презумпция (режим). Итог строки — собственный статус ∧ остаток для «прочие».
Сигналы считаются для пути кода каскада:

| Сигнал | Когда |
|---|---|
| S1 | собственное условие строки пути = F |
| S2 | на пути «прочие», а предшествующий конкретный сосед = T |
| S3 | путь альтернативы весь T: сильный, если у выбранного есть U или F; иначе средний (зона ОПИ 3) |
| S4 | собственное условие строки пути = U |
| S5 | остаток «прочие» на пути = U |
| S6 | описание — ассортимент (сильный); на развилке пути два конкретных соседа = T: сильный, если строка пути среди них не подтверждена, иначе средний — зона ОПИ 3 |
| S7 | на пути режимное условие, а режим не подтверждён |
"""
from __future__ import annotations

from feap.decision.conditions import CaseConditions
from feap.decision.contracts import (
    Condition,
    ConditionKind,
    ConditionStatus,
    Method,
    Signal,
    Status,
)
from feap.decision.logic import and_, residual

T, F, U = Status.T, Status.F, Status.U

#: Политики предрегистрации (§5): вложены друг в друга, других для решения нет.
#: Элемент — (сигнал, сила); сила None — любая.
_P1 = {('S1', None), ('S2', None), ('S3', 'strong'), ('S6', 'strong'), ('S7', None)}
_P2 = _P1 | {('S3', 'medium'), ('S6', 'medium'), ('S4', None)}
_P3 = _P2 | {('S5', None)}
POLICIES: dict[str, frozenset[tuple[str, str | None]]] = {
    'P1': frozenset(_P1), 'P2': frozenset(_P2), 'P3': frozenset(_P3),
}

OwnStatus = tuple[Status, Method, str | None, list[str]]


def evaluate(cc: CaseConditions, own: dict[str, OwnStatus]) -> dict[str, ConditionStatus]:
    """Итоговые статусы всех условий кейса.

    ``own`` — собственные статусы семантических и числовых условий. Режим получает F
    по презумпции «не заявлен», чистое «прочие» — T. Условие без ответа — U.
    """
    out: dict[str, ConditionStatus] = {}

    def own_status(c: Condition) -> OwnStatus:
        if c.kind is ConditionKind.REGIME:
            return F, Method.PRESUMPTION, 'режим не заявлен: подтверждается документом', ['режим не заявлен']
        if c.kind is ConditionKind.RESIDUAL:
            return T, Method.LOGIC, None, []
        return own.get(c.id, (U, Method.FAILURE, 'нет ответа', []))

    def resolve(cid: str) -> ConditionStatus:
        if cid in out:
            return out[cid]
        c = cc.conditions[cid]
        status, method, detail, presumed = own_status(c)
        rest = None
        if c.residual and cid not in cc.preceding:
            rest = U  # соседи до «прочие» не проверялись
        elif c.residual:
            before = [resolve(p).status for p in cc.preceding[cid]]
            rest = residual([*before, *[U] * cc.omitted_preceding.get(cid, 0)])
        out[cid] = ConditionStatus(
            condition_id=cid, status=status if rest is None else and_(status, rest), own=status,
            residual=rest, method=method, detail=detail, presumed=presumed,
        )
        return out[cid]

    for cid in cc.conditions:
        resolve(cid)
    return out


def _specific(c: Condition) -> bool:
    return c.kind in (ConditionKind.SEMANTIC, ConditionKind.NUMERIC)


def compute_signals(
    cc: CaseConditions, statuses: dict[str, ConditionStatus], assortment: Status | None = None,
) -> list[Signal]:
    path = [cc.conditions[cid] for cid in cc.chosen]
    signals: list[Signal] = []

    refuted = [c.id for c in path if _specific(c) and statuses[c.id].own is F]
    if refuted:
        signals.append(Signal(name='S1', strength='strong', conditions=refuted,
                              detail='опровергнуто условие пути'))

    for c in path:
        if statuses[c.id].residual is F:
            confirmed = [p for p in cc.preceding.get(c.id, []) if statuses[p].status is T]
            signals.append(Signal(name='S2', strength='strong', conditions=[c.id, *confirmed],
                                  detail='«прочие», а предшествующий сосед подтверждён'))

    clean = all(statuses[c.id].status is T for c in path)
    for code, alt_path in cc.alternatives.items():
        if alt_path and all(statuses[cid].status is T for cid in alt_path):
            signals.append(Signal(name='S3', strength='medium' if clean else 'strong', conditions=alt_path,
                                  detail=f'подтверждена альтернатива {code}'))

    unknown = [c.id for c in path if _specific(c) and statuses[c.id].own is U]
    if unknown:
        signals.append(Signal(name='S4', strength='medium', conditions=unknown,
                              detail='не установлено условие пути'))

    open_rest = [c.id for c in path if statuses[c.id].residual is U]
    if open_rest:
        signals.append(Signal(name='S5', strength='weak', conditions=open_rest,
                              detail='не установлен остаток «прочие»'))

    for fork in cc.forks:
        confirmed = [cid for cid in fork.considered
                     if cc.conditions[cid].kind is not ConditionKind.RESIDUAL
                     and cc.conditions[cid].kind is not ConditionKind.REGIME
                     and statuses[cid].status is T]
        if len(confirmed) >= 2:
            # Строка пути подтверждена вместе с соседом — тексты пересекаются, решает ОПИ 3
            # («газоанализаторы» и «хроматографы»), как S3 при чистом пути.
            on_path = fork.row in confirmed
            signals.append(Signal(name='S6', strength='medium' if on_path else 'strong', conditions=confirmed,
                                  detail='подтверждены соседи одной развилки'))
    if assortment is T:
        signals.append(Signal(name='S6', strength='strong', conditions=['assortment'],
                              detail='в описании несколько товаров'))

    regime = [c.id for c in path if c.kind is ConditionKind.REGIME]
    if regime:
        signals.append(Signal(name='S7', strength='question', conditions=regime,
                              detail='режим подтверждается документом'))
    return signals


def policy_flags(signals: list[Signal]) -> dict[str, bool]:
    return {
        name: any((s.name, None) in members or (s.name, s.strength) in members for s in signals)
        for name, members in POLICIES.items()
    }
