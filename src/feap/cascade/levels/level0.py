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

        # Always store the product card — L0 can always normalize the description.
        # The cascade continues even if some fields are unknown; later levels (L3-L5)
        # will request clarification if they actually need missing parameters.
        state.product_card = result.model_dump()

        # Only block the cascade if LLM explicitly says the product CANNOT be
        # identified at all (e.g., "white powder" with no indication of what it is).
        # The signal is: missing_info is non-empty AND normalized_description is
        # essentially the same as original (LLM couldn't enrich it).
        if result.clarifying_questions and result.missing_info:
            # Check if LLM managed to determine at least the basic nature of the product.
            # If normalized_description is substantially richer than original, proceed.
            if not _description_enriched(result):
                state.missing_parameters = result.missing_info
                state.clarifying_question = result.clarifying_questions[0]

        return result


def _description_enriched(card: ProductCard) -> bool:
    """Check if LLM managed to determine the basic nature of the product.

    Returns True if we have enough info to proceed with classification:
    - function is determined, OR
    - intended_use is determined, OR
    - material is determined, OR
    - processing_degree is determined
    """
    return bool(
        card.function
        or card.intended_use
        or card.material
        or card.processing_degree
    )
