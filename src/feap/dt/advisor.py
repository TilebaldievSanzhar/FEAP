"""Риск-советник: пороговые сигналы считает код, LLM формулирует и обосновывает.

Cascade + Evidence Grounding (dt_core_dev_plan.md §4):
- compute_signals() — чистая функция, детерминированные эвристики
  (цена/кг-выбросы, отсутствие весов, нетто=брутто, низкий confidence);
- LLM (уровень 94) пишет объяснение + рекомендацию + evidence по КАЖДОМУ
  выданному сигналу и оценивает соответствие описания (31) коду (33);
- harness: советы по несуществующим signal_id и вердикты по несуществующим
  позициям отбрасываются.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feap.config import settings
from feap.db.models import TnvedNode
from feap.dt.schemas import AdviseOut, ShipmentIn
from feap.llm.client import classify as llm_classify
from feap.prompts.registry import render

_LOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class RiskSignal:
    id: str
    kind: str
    severity: str  # warning | info
    message: str
    data: dict[str, str]


def compute_signals(model: ShipmentIn) -> list[RiskSignal]:
    """Детерминированные риск-эвристики по позициям досье."""
    signals: list[RiskSignal] = []
    factor = Decimal(str(settings.DT_PRICE_OUTLIER_FACTOR))

    # ── Цена за килограмм: выбросы против медианы досье ─────────────────────
    price_rows: list[tuple[int, Decimal]] = []
    for idx, item in enumerate(model.items, start=1):
        if item.net_kg and item.net_kg > 0 and item.price > 0:
            price_rows.append((idx, item.price / item.net_kg))
    if len(price_rows) >= 3:
        med = median(v for _, v in price_rows)
        if med > 0:
            for idx, value in price_rows:
                ratio = value / med
                if ratio > factor or ratio < 1 / factor:
                    signals.append(RiskSignal(
                        id=f"S-PRICEKG-{idx}",
                        kind="price_per_kg_outlier",
                        severity="warning",
                        message=(
                            f"Товар {idx}: цена/кг {value:.2f} отклоняется от "
                            f"медианы досье {med:.2f} в {max(ratio, 1 / ratio):.1f} раза"
                        ),
                        data={
                            "position": str(idx),
                            "price_per_kg": f"{value:.4f}",
                            "median": f"{med:.4f}",
                        },
                    ))

    for idx, item in enumerate(model.items, start=1):
        # ── Нет веса нетто: цена/кг непроверяема, риск запроса КТС ──────────
        if item.price > 0 and (item.net_kg is None or item.net_kg == 0):
            signals.append(RiskSignal(
                id=f"S-NONET-{idx}",
                kind="missing_net_weight",
                severity="warning",
                message=f"Товар {idx}: не указан вес нетто — цена/кг непроверяема",
                data={"position": str(idx)},
            ))

        # ── Нетто = брутто: для упакованного товара подозрительно ───────────
        if (
            item.net_kg is not None and item.gross_kg is not None
            and item.net_kg == item.gross_kg and item.net_kg > 0
        ):
            signals.append(RiskSignal(
                id=f"S-NETGROSS-{idx}",
                kind="net_equals_gross",
                severity="info",
                message=(
                    f"Товар {idx}: вес нетто равен брутто ({item.net_kg} кг) — "
                    "для упакованного товара это маловероятно"
                ),
                data={"position": str(idx), "weight": str(item.net_kg)},
            ))

        # ── Низкая уверенность классификации ────────────────────────────────
        classification = (item.extra or {}).get("classification") or {}
        confidence = classification.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < _LOW_CONFIDENCE:
            signals.append(RiskSignal(
                id=f"S-LOWCONF-{idx}",
                kind="low_classification_confidence",
                severity="warning",
                message=(
                    f"Товар {idx}: уверенность классификации {confidence:.2f} — "
                    "код стоит перепроверить"
                ),
                data={"position": str(idx), "confidence": str(confidence)},
            ))

    return signals


# ── LLM-схема (уровень 94) ───────────────────────────────────────────────────

class RiskEvidence(BaseModel):
    source: str = Field(description="Откуда взято: 'позиция 2', 'сигнал S-...', 'документ ...'")
    detail: str


class RiskAdvice(BaseModel):
    signal_id: str = Field(description="id сигнала из входных данных")
    explanation: str = Field(description="Что этот сигнал означает при выпуске/контроле")
    evidence: list[RiskEvidence] = Field(default_factory=list)
    recommendation: str = Field(description="Что сделать декларанту до подачи")


class CodeCheck(BaseModel):
    position: int = Field(description="Номер позиции досье")
    verdict: Literal["consistent", "questionable", "mismatch"]
    reasoning: str = Field(description="Сопоставление описания товара с текстом кода")


class AdvisorResult(BaseModel):
    risk_review: str = Field(
        description="Шаг 1: общий обзор рисков досье по сигналам и позициям"
    )
    advices: list[RiskAdvice] = Field(default_factory=list)
    code_checks: list[CodeCheck] = Field(
        default_factory=list,
        description="Соответствие описания (графа 31) коду (графа 33) — по каждой позиции с кодом",
    )


async def load_code_names(
    session: AsyncSession, model: ShipmentIn
) -> dict[int, tuple[str, str]]:
    """position → (hs_code, имя кода из номенклатуры) для позиций с кодом."""
    codes = {i.hs_code for i in model.items if i.hs_code}
    if not codes:
        return {}
    result = await session.execute(
        select(TnvedNode.code, TnvedNode.name).where(
            TnvedNode.code.in_(codes), TnvedNode.is_leaf.is_(True)
        )
    )
    names = {code: name for code, name in result.all()}
    return {
        idx: (item.hs_code, names[item.hs_code])
        for idx, item in enumerate(model.items, start=1)
        if item.hs_code and item.hs_code in names
    }


async def advise(
    model: ShipmentIn,
    code_names: dict[int, tuple[str, str]],
) -> AdviseOut:
    """Сигналы (код) + формулировки и 31↔33 (LLM) с harness-фильтрацией."""
    signals = compute_signals(model)
    coded_items = [
        {
            "position": idx,
            "description": model.items[idx - 1].description,
            "hs_code": code,
            "code_name": name,
        }
        for idx, (code, name) in sorted(code_names.items())
    ]

    signal_dumps = [asdict(s) for s in signals]
    if not signals and not coded_items:
        return AdviseOut(signals=[], llm_used=False)

    system_prompt = render("system/dt_advisor.jinja2")
    user_message = render(
        "context/dt_advisor_input.jinja2",
        signals=signal_dumps,
        coded_items=coded_items,
    )
    result = await llm_classify(
        level=94,
        system_prompt=system_prompt,
        user_message=user_message,
        output_model=AdvisorResult,
    )

    # ── Harness: только выданные сигналы и существующие позиции ─────────────
    dropped: list[str] = []
    known_signals = {s.id for s in signals}
    known_positions = {c["position"] for c in coded_items}

    advices_out = []
    for advice in result.advices:
        if advice.signal_id not in known_signals:
            dropped.append(f"advice: неизвестный signal_id «{advice.signal_id}»")
            continue
        advices_out.append(advice.model_dump(mode="json"))

    code_checks_out = []
    for check in result.code_checks:
        if check.position not in known_positions:
            dropped.append(f"code_check: позиция {check.position} без кода/не существует")
            continue
        code_checks_out.append(check.model_dump(mode="json"))

    return AdviseOut(
        signals=signal_dumps,
        advices=advices_out,
        code_checks=code_checks_out,
        dropped=dropped,
        llm_used=True,
    )
