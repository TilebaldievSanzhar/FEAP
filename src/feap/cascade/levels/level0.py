"""Level 0: Product description enrichment."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from feap.cascade.levels.base import BaseCascadeLevel, CascadeState
from feap.llm.client import classify
from feap.prompts.assembler import PromptAssembler
from feap.schemas.product_card import ProductCard


class Level0Enrichment(BaseCascadeLevel):
    level = 0

    def get_output_model(self):
        return ProductCard

    async def run(self, state: CascadeState, db: AsyncSession) -> ProductCard:
        system, user = PromptAssembler.level0(state.product_description)
        result = await classify(
            level=0,
            system_prompt=system,
            user_message=user,
            output_model=ProductCard,
        )

        # If critical info is missing and LLM generated clarifying questions,
        # do NOT store product_card — signal needs_input to the orchestrator.
        if result.clarifying_questions and result.missing_info:
            state.missing_parameters = result.missing_info
            state.clarifying_question = result.clarifying_questions[0]
            # product_card stays None → orchestrator will raise _NeedsInput
            return result

        state.product_card = result.model_dump()

        if result.missing_info:
            state.missing_parameters = result.missing_info
        if result.clarifying_questions:
            state.clarifying_question = result.clarifying_questions[0]

        return result
