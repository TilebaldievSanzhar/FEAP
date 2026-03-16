"""NTM verifier: LLM-based dual matching (code + product description)."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from feap.db.models import TnvedNode
from feap.db.queries import get_ntm_measures_for_code
from feap.llm.client import classify
from feap.ntm.schemas import (
    NtmVerdict,
    NtmVerificationLLMResponse,
    NtmVerifiedMeasure,
    NtmVerifyResponse,
)
from feap.prompts.registry import render

logger = logging.getLogger(__name__)


async def verify_ntm_measures(
    db: AsyncSession,
    code: str,
    product_description: str,
    node: TnvedNode | None = None,
) -> NtmVerifyResponse:
    """Verify NTM measures for a code against product description.

    1. Lookup all measures by code (prefix matching)
    2. If measures with qualifiers exist, call LLM for dual matching
    3. Return measures with verified statuses
    """
    rows = await get_ntm_measures_for_code(db, code)

    if not rows:
        return NtmVerifyResponse(
            code=code,
            code_formatted=node.code_formatted if node else None,
            name=node.name if node else None,
            product_description=product_description,
            verified=True,
        )

    # Build measure list for LLM
    measure_infos: list[NtmVerifiedMeasure] = []
    has_qualifiers = False

    for measure, measure_code, measure_type in rows:
        if measure_code.product_description:
            has_qualifiers = True
        measure_infos.append(NtmVerifiedMeasure(
            measure_type=measure_type.name_ru,
            measure_type_kind=measure_type.kind,
            name=measure.name,
            status="may_apply",
            qualifier=measure_code.product_description,
            legal_basis=measure.legal_basis,
            note=measure.note,
            scope=measure.scope,
            valid_to=measure.valid_to.isoformat() if measure.valid_to else None,
        ))

    # If no qualifiers — all measures apply by code match alone
    if not has_qualifiers:
        for m in measure_infos:
            m.status = "applies"
            m.reasoning = "Квалификатор отсутствует — мера применяется по совпадению кода"
        return _build_response(code, node, product_description, measure_infos, verified=True)

    # LLM verification
    verdicts = await _llm_verify(product_description, measure_infos)

    # Apply verdicts
    for verdict in verdicts:
        if 0 <= verdict.measure_index < len(measure_infos):
            mi = measure_infos[verdict.measure_index]
            mi.status = verdict.status
            mi.reasoning = verdict.reasoning

    # Measures without qualifier that weren't verified — mark as applies
    for mi in measure_infos:
        if mi.status == "may_apply" and not mi.qualifier:
            mi.status = "applies"
            mi.reasoning = "Квалификатор отсутствует — мера применяется по совпадению кода"

    return _build_response(code, node, product_description, measure_infos, verified=True)


async def _llm_verify(
    product_description: str,
    measures: list[NtmVerifiedMeasure],
) -> list[NtmVerdict]:
    """Call LLM to verify measures against product description."""
    system = render("system/ntm_verify.jinja2")

    # Build user message
    lines = [f"ТОВАР: {product_description}", "", "МЕРЫ НТР:"]
    for i, m in enumerate(measures):
        qualifier_text = f" | Квалификатор: {m.qualifier}" if m.qualifier else ""
        lines.append(f"  [{i}] {m.measure_type}: {m.name}{qualifier_text}")

    user = "\n".join(lines)

    try:
        result = await classify(
            level=99,  # special level for NTM verification
            system_prompt=system,
            user_message=user,
            output_model=NtmVerificationLLMResponse,
            model_override=None,  # uses light model (level 99 not in heavy/medium)
        )
        return result.verdicts
    except Exception:
        logger.warning("LLM NTM verification failed, falling back to may_apply", exc_info=True)
        return []


_CATEGORY_FLAGS = {
    "prohibition": "has_prohibitions",
    "licensing": "has_licensing",
    "technical_regulation": "has_technical_regulations",
    "certification": "has_technical_regulations",
    "sanitary": "has_sanitary_control",
    "veterinary": "has_veterinary_control",
    "phytosanitary": "has_phytosanitary_control",
    "marking": "has_marking",
}


def _build_response(
    code: str,
    node: TnvedNode | None,
    product_description: str,
    measures: list[NtmVerifiedMeasure],
    *,
    verified: bool,
) -> NtmVerifyResponse:
    """Build the final NTM verification response."""
    # We need category info to set flags — extract from kind
    _KIND_TO_CATEGORY = {
        1: "prohibition", 2: "prohibition",
        3: "quota", 4: "licensing",
        5: "certification", 6: "technical_regulation",
        7: "sanitary", 8: "veterinary", 9: "phytosanitary",
        10: "other", 11: "marking", 12: "marking", 13: "other",
    }

    flags: dict[str, bool] = {}
    for m in measures:
        if m.status in ("applies", "uncertain", "may_apply"):
            category = _KIND_TO_CATEGORY.get(m.measure_type_kind, "other")
            flag_attr = _CATEGORY_FLAGS.get(category)
            if flag_attr:
                flags[flag_attr] = True

    return NtmVerifyResponse(
        code=code,
        code_formatted=node.code_formatted if node else None,
        name=node.name if node else None,
        product_description=product_description,
        measures=measures,
        total_count=len(measures),
        verified=verified,
        has_prohibitions=flags.get("has_prohibitions", False),
        has_licensing=flags.get("has_licensing", False),
        has_technical_regulations=flags.get("has_technical_regulations", False),
        has_sanitary_control=flags.get("has_sanitary_control", False),
        has_veterinary_control=flags.get("has_veterinary_control", False),
        has_phytosanitary_control=flags.get("has_phytosanitary_control", False),
        has_marking=flags.get("has_marking", False),
    )
