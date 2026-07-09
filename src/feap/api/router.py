"""API router with classification endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.api.deps import get_db, get_orchestrator
from feap.auth.deps import AuthContext, get_auth_context
from feap.cascade.orchestrator import CascadeOrchestrator, SessionNotFound
from feap.config import settings
from feap.usage import record_usage
from feap.db.models import TnvedNode
from feap.db.queries import get_excise_rate, get_ntm_measures_for_code, get_vat_exemption
from feap.ntm.schemas import NtmVerifyRequest, NtmVerifyResponse
from feap.ntm.verifier import verify_ntm_measures
from feap.schemas.api import (
    AnswerRequest,
    ClassifyRequest,
    ClassifyResponse,
    CodeLookupResponse,
    ExciseInfo,
    NtmMeasureInfo,
    NtmResponse,
    NtmSummary,
    TnvedNodeResponse,
    VatExemptionInfo,
    VatInfo,
)

router = APIRouter(prefix="/api/v1")


# ── NTM helpers ──────────────────────────────────────────────────────────────

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


async def _build_ntm_summary(db: AsyncSession, code: str) -> NtmSummary:
    """Build NTM summary for a 10-digit code."""
    rows = await get_ntm_measures_for_code(db, code)

    measures: list[NtmMeasureInfo] = []
    flags: dict[str, bool] = {}

    for measure, measure_code, measure_type in rows:
        info = NtmMeasureInfo(
            measure_type=measure_type.name_ru,
            measure_type_kind=measure_type.kind,
            name=measure.name,
            status="may_apply",
            qualifier=measure_code.product_description,
            legal_basis=measure.legal_basis,
            note=measure.note,
            scope=measure.scope,
            valid_to=measure.valid_to.isoformat() if measure.valid_to else None,
        )
        measures.append(info)

        flag_attr = _CATEGORY_FLAGS.get(measure_type.category)
        if flag_attr:
            flags[flag_attr] = True

    return NtmSummary(
        measures=measures,
        total_count=len(measures),
        has_prohibitions=flags.get("has_prohibitions", False),
        has_licensing=flags.get("has_licensing", False),
        has_technical_regulations=flags.get("has_technical_regulations", False),
        has_sanitary_control=flags.get("has_sanitary_control", False),
        has_veterinary_control=flags.get("has_veterinary_control", False),
        has_phytosanitary_control=flags.get("has_phytosanitary_control", False),
        has_marking=flags.get("has_marking", False),
        note=(
            "Применимость мер зависит не только от кода ТН ВЭД, но и от описания товара. "
            "Проверьте квалификаторы (qualifier) каждой меры."
        ) if measures else None,
    )


@router.post("/classify", response_model=ClassifyResponse)
async def classify_product(
    request: ClassifyRequest,
    db: AsyncSession = Depends(get_db),
    orchestrator: CascadeOrchestrator = Depends(get_orchestrator),
    ctx: AuthContext = Depends(get_auth_context),
) -> ClassifyResponse:
    """Classify a product by its description.

    Returns either a completed classification or a request for additional info.
    """
    response = await orchestrator.classify(
        description=request.description,
        db=db,
        additional_info=request.additional_info or None,
    )
    # Метеринг: одна классификация = одно событие; resume (answer) не тарифицируется.
    record_usage(
        db, org_id=ctx.org_id, kind="classification", quantity=1,
        ref_type="classify", ref_id=response.session_id,
        meta={"status": response.status},
    )
    await db.commit()
    return response


@router.post("/classify/{session_id}/answer", response_model=ClassifyResponse)
async def answer_question(
    session_id: str,
    request: AnswerRequest,
    db: AsyncSession = Depends(get_db),
    orchestrator: CascadeOrchestrator = Depends(get_orchestrator),
) -> ClassifyResponse:
    """Provide answers to clarifying questions from a previous classification.

    Resumes the cascade from the level where it stopped, using the saved state.
    """
    try:
        return await orchestrator.resume(
            session_id=session_id,
            answers=request.answers,
            db=db,
        )
    except SessionNotFound:
        raise HTTPException(
            status_code=404,
            detail=f"Сессия {session_id} не найдена или истекла.",
        )


@router.get("/tnved/tree")
async def get_tree(
    level: int = 0,
    parent_code: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[TnvedNodeResponse]:
    """Browse the ТН ВЭД tree.

    - level=0 returns sections
    - parent_code returns children of that node
    """
    if parent_code:
        parent = await db.execute(
            select(TnvedNode).where(TnvedNode.code == parent_code).limit(1)
        )
        parent_node = parent.scalar_one_or_none()
        if not parent_node:
            raise HTTPException(404, f"Code {parent_code} not found")

        result = await db.execute(
            select(TnvedNode).where(TnvedNode.parent_id == parent_node.id).order_by(TnvedNode.code)
        )
    else:
        result = await db.execute(
            select(TnvedNode).where(TnvedNode.level == level).order_by(TnvedNode.code)
        )

    nodes = result.scalars().all()

    # Get children counts
    responses = []
    for node in nodes:
        count_result = await db.execute(
            select(func.count()).select_from(TnvedNode).where(TnvedNode.parent_id == node.id)
        )
        children_count = count_result.scalar() or 0

        responses.append(TnvedNodeResponse(
            code=node.code,
            code_formatted=node.code_formatted,
            level=node.level,
            name=node.name,
            duty_rate=node.duty_rate,
            children_count=children_count,
        ))

    return responses


@router.get("/tnved/lookup/{code}", response_model=CodeLookupResponse)
async def lookup_code(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> CodeLookupResponse:
    """Справка по 10-значному коду ТН ВЭД: тариф, НДС, акциз и т.д."""
    if not code.isdigit() or len(code) != 10:
        raise HTTPException(400, "Код должен состоять ровно из 10 цифр.")

    result = await db.execute(
        select(TnvedNode).where(TnvedNode.code == code, TnvedNode.is_leaf.is_(True)).limit(1)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, f"Код {code} не найден в номенклатуре.")

    # Determine VAT rate and exemption
    exemption = await get_vat_exemption(db, code)
    if exemption:
        vat = VatInfo(
            rate=exemption.rate_override or "0%",
            exemption=VatExemptionInfo(
                type=exemption.exemption_type,
                legal_ref=exemption.legal_ref,
                note=exemption.note,
                valid_to=exemption.valid_to.isoformat() if exemption.valid_to else None,
            ),
        )
    else:
        vat = VatInfo(rate=settings.VAT_RATE_DEFAULT)

    # Determine excise
    excise_row = await get_excise_rate(db, code)
    if excise_row:
        excise = ExciseInfo(
            applicable=True,
            product_name=excise_row.product_name,
            rate=excise_row.rate,
            rate_unit=excise_row.rate_unit,
            legal_ref=excise_row.legal_ref,
            note=excise_row.note,
        )
    else:
        excise = ExciseInfo(applicable=False)

    # NTM measures
    ntm = await _build_ntm_summary(db, code)

    return CodeLookupResponse(
        code=node.code,
        code_formatted=node.code_formatted or code,
        name=node.name,
        duty_rate=node.duty_rate,
        vat=vat,
        excise=excise,
        unit_primary=node.unit_primary,
        unit_secondary=node.unit_secondary,
        ntm=ntm if ntm.total_count > 0 else None,
    )


@router.get("/ntm/{code}", response_model=NtmResponse)
async def get_ntm_measures(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> NtmResponse:
    """Нетарифные меры регулирования для кода ТН ВЭД.

    Возвращает все применимые меры: технические регламенты, лицензирование,
    санитарный/ветеринарный/фитосанитарный контроль, маркировка и т.д.
    """
    if not code.isdigit() or len(code) != 10:
        raise HTTPException(400, "Код должен состоять ровно из 10 цифр.")

    result = await db.execute(
        select(TnvedNode).where(TnvedNode.code == code, TnvedNode.is_leaf.is_(True)).limit(1)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, f"Код {code} не найден в номенклатуре.")

    ntm = await _build_ntm_summary(db, code)

    return NtmResponse(
        code=node.code,
        code_formatted=node.code_formatted,
        name=node.name,
        ntm=ntm,
    )


@router.post("/ntm/verify", response_model=NtmVerifyResponse)
async def verify_ntm(
    request: NtmVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> NtmVerifyResponse:
    """Верификация мер НТР по двойному соответствию (код + описание товара).

    LLM проверяет, подпадает ли конкретный товар под каждую меру,
    сопоставляя описание товара с квалификаторами мер из НПА.

    Статусы:
    - applies: мера точно применяется к товару
    - does_not_apply: мера не применяется (товар не подпадает под квалификатор)
    - uncertain: недостаточно информации для однозначного решения
    """
    if not request.code.isdigit() or len(request.code) != 10:
        raise HTTPException(400, "Код должен состоять ровно из 10 цифр.")

    result = await db.execute(
        select(TnvedNode).where(
            TnvedNode.code == request.code, TnvedNode.is_leaf.is_(True)
        ).limit(1)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, f"Код {request.code} не найден в номенклатуре.")

    return await verify_ntm_measures(
        db=db,
        code=request.code,
        product_description=request.product_description,
        node=node,
    )


@router.get("/tnved/code/{code}")
async def get_code_details(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> TnvedNodeResponse:
    """Get details for a specific ТН ВЭД code."""
    result = await db.execute(
        select(TnvedNode).where(TnvedNode.code == code).limit(1)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(404, f"Code {code} not found")

    count_result = await db.execute(
        select(func.count()).select_from(TnvedNode).where(TnvedNode.parent_id == node.id)
    )
    children_count = count_result.scalar() or 0

    return TnvedNodeResponse(
        code=node.code,
        code_formatted=node.code_formatted,
        level=node.level,
        name=node.name,
        duty_rate=node.duty_rate,
        children_count=children_count,
    )
