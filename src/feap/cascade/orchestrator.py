"""Cascade orchestrator: runs levels sequentially with backtracking support."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.backtracker import BacktrackLimitExceeded, apply_backtrack, _infer_target_level
from feap.cascade.levels.base import CascadeState
from feap.cascade.evidence import evidence_records
from feap.cascade.levels.level0 import Level0Enrichment
from feap.cascade.levels.level1 import Level1Section
from feap.cascade.levels.level2 import Level2Group
from feap.cascade.levels.level3 import Level3Position
from feap.cascade.levels.level4 import Level4Subposition
from feap.cascade.levels.level5 import Level5Subsubposition
from feap.cascade.validator import validate_and_build_result
from feap.config import settings
from feap.db.queries import delete_session, load_session, save_session
from feap.schemas.api import ClassifyResponse
from feap.schemas.validation import ClassificationResult, PartialResult

logger = logging.getLogger(__name__)

LEVELS = [
    Level0Enrichment(),
    Level1Section(),
    Level2Group(),
    Level3Position(),
    Level4Subposition(),
    Level5Subsubposition(),
]


def _infer_target_level_quick(trigger) -> int:
    """Quick target level inference without full backtrack logic."""
    return _infer_target_level(trigger)


def _parent_state_exists(state: CascadeState, target_level: int) -> bool:
    """Check that parent state is set for the target level to restart from."""
    if target_level <= 1:
        return True  # L1 has no parent dependency
    if target_level == 2:
        return state.section is not None
    if target_level == 3:
        return state.group is not None
    if target_level == 4:
        return state.position is not None
    if target_level == 5:
        return state.subposition is not None
    return True


class CascadeOrchestrator:
    """Runs the full classification cascade."""

    async def classify(
        self,
        description: str,
        db: AsyncSession,
        additional_info: dict[str, str] | None = None,
    ) -> ClassifyResponse:
        session_id = str(uuid.uuid4())
        state = CascadeState(description)

        if additional_info:
            state.product_description += "\n" + "\n".join(
                f"{k}: {v}" for k, v in additional_info.items()
            )

        try:
            result = await self._run_cascade(state, db, start_level_idx=0)
            return ClassifyResponse(
                session_id=session_id,
                status="completed",
                result=result,
            )
        except _NeedsInput as e:
            state.stopped_at_level = e.partial.determined_to_level
            await save_session(db, session_id, state.to_dict())
            return ClassifyResponse(
                session_id=session_id,
                status="needs_input",
                partial=e.partial,
            )
        except BacktrackLimitExceeded as e:
            return ClassifyResponse(
                session_id=session_id,
                status="error",
                error=str(e),
            )
        except Exception as e:
            logger.exception("Cascade error")
            return ClassifyResponse(
                session_id=session_id,
                status="error",
                error=str(e),
            )

    async def resume(
        self,
        session_id: str,
        answers: dict[str, str],
        db: AsyncSession,
    ) -> ClassifyResponse:
        """Resume a paused classification with user-provided answers."""
        state_dict = await load_session(db, session_id)
        if state_dict is None:
            raise SessionNotFound(session_id)

        state = CascadeState.from_dict(state_dict)

        # Check answer iteration limit
        state.answer_count += 1
        if state.answer_count > settings.SESSION_MAX_ANSWERS:
            await delete_session(db, session_id)
            return ClassifyResponse(
                session_id=session_id,
                status="error",
                error=f"Превышен лимит уточнений ({settings.SESSION_MAX_ANSWERS}). "
                      f"Попробуйте заново с более полным описанием.",
            )

        # Merge answers into product description and product card
        answers_text = "\n".join(f"{k}: {v}" for k, v in answers.items())
        state.product_description += "\n" + answers_text

        if state.product_card is not None:
            # Merge answers into product card as additional fields
            for key, value in answers.items():
                state.product_card[key] = value
            # L4/L5 cite authoritative input. Custom dictionary keys alone were
            # not rendered by product_card.jinja2, so answers could be invisible.
            state.product_card['original_description'] = state.product_description
        else:
            # L0 stopped before creating product_card — answers go into description only
            pass

        # Clear needs_input signals
        state.missing_parameters = []
        state.clarifying_question = None

        # Determine which level to resume from
        resume_level = state.stopped_at_level or 0
        start_idx = next(
            (i for i, lv in enumerate(LEVELS) if lv.level >= resume_level),
            0,
        )

        try:
            result = await self._run_cascade(state, db, start_level_idx=start_idx)
            await delete_session(db, session_id)
            return ClassifyResponse(
                session_id=session_id,
                status="completed",
                result=result,
            )
        except _NeedsInput as e:
            state.stopped_at_level = e.partial.determined_to_level
            await save_session(db, session_id, state.to_dict())
            return ClassifyResponse(
                session_id=session_id,
                status="needs_input",
                partial=e.partial,
            )
        except BacktrackLimitExceeded as e:
            await delete_session(db, session_id)
            return ClassifyResponse(
                session_id=session_id,
                status="error",
                error=str(e),
            )
        except Exception as e:
            logger.exception("Cascade resume error")
            await delete_session(db, session_id)
            return ClassifyResponse(
                session_id=session_id,
                status="error",
                error=str(e),
            )

    async def _run_cascade(
        self,
        state: CascadeState,
        db: AsyncSession,
        start_level_idx: int = 0,
    ) -> ClassificationResult:
        current_level_idx = start_level_idx
        backtrack_count = 0

        while current_level_idx < len(LEVELS):
            level = LEVELS[current_level_idx]
            logger.info("Running level %d (%s)", level.level, type(level).__name__)

            result = await level.run(state, db)

            # Check for backtrack trigger
            trigger = getattr(result, "backtrack_trigger", None)
            if trigger is not None:
                target = _infer_target_level_quick(trigger)
                # L2 can reject a group before committing any group decision.
                # Let apply_backtrack perform its L2->L1 escalation in this case;
                # ignoring the trigger would advance to L3 with state.group=None.
                group_reselection = target == level.level == 2
                if target >= level.level and not group_reselection:
                    logger.warning(
                        "Ignoring backtrack trigger from level %d with target %d "
                        "(target must be < current level). excluded_code='%s'",
                        level.level, target, trigger.excluded_code,
                    )
                elif not _parent_state_exists(state, target):
                    logger.warning(
                        "Ignoring backtrack trigger targeting level %d: "
                        "parent state not set (section=%s, group=%s, position=%s)",
                        target, state.section, state.group, state.position,
                    )
                else:
                    backtrack_count += 1
                    target = apply_backtrack(state, trigger, backtrack_count)
                    # Find the level index to restart from
                    current_level_idx = next(
                        i for i, lv in enumerate(LEVELS) if lv.level >= target
                    )
                    logger.info("Backtracking to level %d (attempt %d)", target, backtrack_count)
                    continue

            # Generalized needs_input check — works for any level
            if state.missing_parameters:
                # Determine the furthest code reached so far
                partial_code = (
                    state.code_10
                    or state.subposition
                    or state.position
                    or state.group
                    or state.section
                    or ""
                )
                # Build question list from parameters
                questions = [
                    f"Укажите: {param}" for param in state.missing_parameters
                ]
                raise _NeedsInput(PartialResult(
                    partial_code=partial_code,
                    determined_to_level=level.level,
                    missing_parameters=state.missing_parameters,
                    question=state.clarifying_question or "Необходимы дополнительные данные",
                    questions=questions,
                    classification_chain_so_far=list(state.chain),
                    evidence_checks=evidence_records(result),
                ))

            current_level_idx += 1

        # All levels complete — validate
        return await validate_and_build_result(state, db)


class _NeedsInput(Exception):
    def __init__(self, partial: PartialResult):
        self.partial = partial


class SessionNotFound(Exception):
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session {session_id} not found")
