"""Per-case outcome: what the declarant would have received.

    correct       ✅ the code is right (later: payments and permits too)
    safe_stop     🟡 a question instead of a code, or a wrong code the system flagged
    silent_error  🔴 a wrong code presented without any warning
    failure       ⚫ error, timeout, budget — no answer at all

Confidence alone is not a warning: in v27 the calibration error (ECE) was 0.43.
A case counts as flagged only by what the user sees in the result —
requires_human_review or risk_flags.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum

from benchmark.evaluator.budget import is_budget_error
from benchmark.metrics.stats import share
from benchmark.schema.case_v2 import CaseV2, Sufficiency
from benchmark.schema.prediction import CasePrediction


class Outcome(str, Enum):
    CORRECT = 'correct'
    SAFE_STOP = 'safe_stop'
    SILENT_ERROR = 'silent_error'
    FAILURE = 'failure'


ICONS: dict[Outcome, str] = {
    Outcome.CORRECT: '✅', Outcome.SAFE_STOP: '🟡',
    Outcome.SILENT_ERROR: '🔴', Outcome.FAILURE: '⚫',
}

_QUESTION_REASON: dict[Sufficiency, str] = {
    Sufficiency.INSUFFICIENT: 'question_justified',
    Sufficiency.SUFFICIENT: 'question_unneeded',
}


@dataclass
class CaseOutcome:
    case_id: str
    outcome: Outcome
    reason: str
    scored: bool
    gold_code: str
    predicted_code: str | None
    flagged: bool
    confidence: float | None
    verification: str
    sufficiency: str
    difficulty: str | None
    section: str | None
    missing_parameters: list[str] = field(default_factory=list)

    @property
    def wrong_code(self) -> bool:
        """A code was returned and it is not the gold one."""
        return self.predicted_code is not None and self.outcome is not Outcome.CORRECT

    def to_json(self) -> dict:
        return {**asdict(self), 'outcome': self.outcome.value}


def is_flagged(pred: CasePrediction) -> bool:
    result = (pred.raw_response or {}).get('result') or {}
    return bool(result.get('requires_human_review') or result.get('risk_flags'))


def case_outcome(case: CaseV2, pred: CasePrediction | None) -> CaseOutcome:
    gold = case.gold.classification
    if gold is None:
        raise ValueError(f'{case.id}: no classification gold')
    base = dict(
        case_id=case.id, scored=gold.scored, gold_code=gold.code_10,
        verification=gold.verification.value, sufficiency=gold.sufficiency.value,
        difficulty=case.difficulty, section=case.section,
    )
    if pred is None:
        return CaseOutcome(outcome=Outcome.FAILURE, reason='not_run', predicted_code=None,
                           flagged=False, confidence=None, **base)

    code = pred.predicted_code_10 if pred.status == 'completed' else None
    common = dict(
        predicted_code=code, flagged=is_flagged(pred), confidence=pred.predicted_confidence,
        missing_parameters=list(pred.missing_parameters), **base,
    )
    if pred.status == 'needs_input':
        reason = _QUESTION_REASON.get(gold.sufficiency, 'question')
        return CaseOutcome(outcome=Outcome.SAFE_STOP, reason=reason, **common)
    if pred.status != 'completed':
        message = pred.error_message or ''
        reason = ('budget' if is_budget_error(message)
                  else 'timeout' if message.startswith('Timeout') else 'error')
        return CaseOutcome(outcome=Outcome.FAILURE, reason=reason, **common)
    if code is None:
        return CaseOutcome(outcome=Outcome.FAILURE, reason='no_code', **common)
    if gold.accepts(code):
        reason = 'exact' if code == gold.code_10 else 'acceptable'
        return CaseOutcome(outcome=Outcome.CORRECT, reason=reason, **common)
    if common['flagged']:
        return CaseOutcome(outcome=Outcome.SAFE_STOP, reason='flagged_wrong', **common)
    return CaseOutcome(outcome=Outcome.SILENT_ERROR, reason='wrong_unflagged', **common)


def _counts(rows: list[CaseOutcome]) -> dict:
    return {'n': len(rows), **{o.value: sum(r.outcome is o for r in rows) for o in Outcome}}


def summarize(outcomes: list[CaseOutcome]) -> dict:
    """Headline over scored cases; quarantined and disputed gold listed apart."""
    scored = [o for o in outcomes if o.scored]
    n = len(scored)
    counts = Counter(o.outcome for o in scored)
    wrong = [o for o in scored if o.wrong_code]
    right = [o for o in scored if o.outcome is Outcome.CORRECT]
    return {
        'n_cases': len(outcomes),
        'n_scored': n,
        'not_scored': sorted(o.case_id for o in outcomes if not o.scored),
        'outcomes': {o.value: share(counts[o], n) for o in Outcome},
        'reasons': dict(sorted(Counter(o.reason for o in scored).items())),
        'flags': {
            'wrong_codes': len(wrong),
            'flag_recall_on_wrong': share(sum(o.flagged for o in wrong), len(wrong)),
            'false_alarm_on_correct': share(sum(o.flagged for o in right), len(right)),
        },
        'by_difficulty': {
            d: _counts([o for o in scored if o.difficulty == d])
            for d in ('easy', 'medium', 'hard') if any(o.difficulty == d for o in scored)
        },
        'by_verification': {
            v: _counts([o for o in scored if o.verification == v])
            for v in sorted({o.verification for o in scored})
        },
        'silent_errors': [o.case_id for o in scored if o.outcome is Outcome.SILENT_ERROR],
    }
