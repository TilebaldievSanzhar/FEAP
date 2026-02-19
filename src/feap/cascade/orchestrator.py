"""Cascade orchestrator: runs levels sequentially with backtracking support."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.backtracker import BacktrackLimitExceeded, apply_backtrack
from feap.cascade.levels.base import CascadeState
from feap.cascade.levels.level0 import Level0Enrichment
from feap.cascade.levels.level1 import Level1Section
from feap.cascade.levels.level2 import Level2Group
from feap.cascade.levels.level3 import Level3Position
from feap.cascade.levels.level4 import Level4Subposition
from feap.cascade.levels.level5 import Level5Subsubposition
from feap.cascade.validator import validate_and_build_result
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
            result = await self._run_cascade(state, db)
            return ClassifyResponse(
                session_id=session_id,
                status="completed",
                result=result,
            )
        except _NeedsInput as e:
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

    async def _run_cascade(
        self,
        state: CascadeState,
        db: AsyncSession,
    ) -> ClassificationResult:
        current_level_idx = 0
        backtrack_count = 0

        while current_level_idx < len(LEVELS):
            level = LEVELS[current_level_idx]
            logger.info("Running level %d (%s)", level.level, type(level).__name__)

            result = await level.run(state, db)

            # Check for backtrack trigger
            trigger = getattr(result, "backtrack_trigger", None)
            if trigger is not None:
                backtrack_count += 1
                target = apply_backtrack(state, trigger, backtrack_count)
                # Find the level index to restart from
                current_level_idx = next(
                    i for i, lv in enumerate(LEVELS) if lv.level >= target
                )
                logger.info("Backtracking to level %d (attempt %d)", target, backtrack_count)
                continue

            # Check if level 0 needs user input
            if level.level == 0 and state.clarifying_question and not state.product_card:
                raise _NeedsInput(PartialResult(
                    partial_code="",
                    determined_to_level=0,
                    missing_parameters=state.missing_parameters,
                    question=state.clarifying_question,
                ))

            # Check if level 5 needs user input (missing technical parameters)
            if level.level == 5 and state.missing_parameters:
                raise _NeedsInput(PartialResult(
                    partial_code=state.subposition or "",
                    determined_to_level=4,
                    missing_parameters=state.missing_parameters,
                    question=state.clarifying_question or "Необходимы дополнительные данные",
                    classification_chain_so_far=[e for e in state.chain],
                ))

            current_level_idx += 1

        # All levels complete — validate
        return await validate_and_build_result(state, db)


class _NeedsInput(Exception):
    def __init__(self, partial: PartialResult):
        self.partial = partial
