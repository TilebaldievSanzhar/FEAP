"""TULPAR: проверка разрешительных документов по реестрам «единого окна» КР.

Спека §6.5 и риск R-05: поиск по публичным реестрам swis2.trade.kg
(сертификаты/декларации ТР, СГР, лицензии). Два уровня, честно разделённые:

1. **Формат номера** — детерминированная проверка, работает всегда.
2. **HTTP-поиск по реестру** — только если настроен TULPAR_SEARCH_URL.
   У реестров нет документированного API, формат ответа подтверждается на
   кастдеве, поэтому HTTP-клиент — осознанная эвристика (совпадение номера
   в JSON-ответе) за узким интерфейсом: заменяется без правки остального.

Результат сохраняется в shipment_documents.extracted["permit_check"] и
кэшируется TULPAR_CACHE_HOURS; ФЛК-правило W-TULPAR подхватывает плохие
статусы через графу 44 (DocEntry.permit_status). LLM здесь нет.

Статусы:
- format_invalid — номер не похож на номер документа (плохой);
- format_ok     — формат корректен, реестр не настроен (нейтральный);
- found         — номер найден в реестре;
- not_found     — реестр отвечал, номер не найден (плохой);
- status_mismatch / code_mismatch — зарезервированы до подтверждения
  формата ответа реестра;
- unavailable   — реестр настроен, но запрос не удался (нейтральный).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from feap.config import settings
from feap.dt import documents

logger = logging.getLogger(__name__)

#: Типы документов, которые имеет смысл проверять по реестрам.
VERIFIABLE_KINDS: tuple[str, ...] = ("permit", "cert_origin")

_NUMBER_MIN_LEN = 5
_NUMBER_MAX_LEN = 64
_FORBIDDEN_RE = re.compile(r"[\r\n\t]")


class PermitKindError(ValueError):
    """Документ этого типа не проверяется по реестрам разрешительных."""


@dataclass(frozen=True)
class PermitCheckResult:
    status: str
    detail: str
    source: str            # 'format' | 'registry'
    checked_at: str        # ISO
    cached: bool = False


# ── Уровень 1: формат номера (детерминированно, всегда доступно) ─────────────

def check_number_format(number: str | None) -> tuple[bool, str]:
    """(ok, пояснение). Мягкие эвристики: не валидируем структуру серий —
    форматы номеров ЕАЭС/КР различаются по видам документов."""
    value = (number or "").strip()
    if not value:
        return False, "у документа не указан номер"
    if len(value) < _NUMBER_MIN_LEN:
        return False, f"номер короче {_NUMBER_MIN_LEN} символов"
    if len(value) > _NUMBER_MAX_LEN:
        return False, f"номер длиннее {_NUMBER_MAX_LEN} символов"
    if _FORBIDDEN_RE.search(value):
        return False, "номер содержит переносы строк/табуляцию"
    if not any(ch.isdigit() for ch in value):
        return False, "номер не содержит ни одной цифры"
    return True, "формат номера корректен"


# ── Уровень 2: HTTP-поиск по реестру ─────────────────────────────────────────

def _normalize(number: str) -> str:
    return re.sub(r"\s+", "", number).upper()


async def search_registry(number: str) -> PermitCheckResult:
    """GET {TULPAR_SEARCH_URL}?query=<номер> → found/not_found/unavailable.

    Матчинг — вхождение нормализованного номера в JSON-ответ: до
    подтверждения формата реестра это максимум честного. Кэш и persist —
    у вызывающего (verify_permit).
    """
    import httpx

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    url = settings.TULPAR_SEARCH_URL.strip()
    try:
        async with httpx.AsyncClient(timeout=settings.TULPAR_TIMEOUT_S) as client:
            response = await client.get(url, params={"query": number})
        if response.status_code != 200:
            return PermitCheckResult(
                status="unavailable",
                detail=f"реестр ответил HTTP {response.status_code}",
                source="registry", checked_at=now,
            )
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return PermitCheckResult(
                status="unavailable",
                detail="формат ответа реестра не распознан (не JSON)",
                source="registry", checked_at=now,
            )
        haystack = _normalize(json.dumps(payload, ensure_ascii=False))
        if _normalize(number) in haystack:
            return PermitCheckResult(
                status="found",
                detail="номер найден в реестре",
                source="registry", checked_at=now,
            )
        return PermitCheckResult(
            status="not_found",
            detail="реестр не содержит документа с таким номером",
            source="registry", checked_at=now,
        )
    except httpx.HTTPError as exc:
        logger.warning("TULPAR request failed: %s", exc)
        return PermitCheckResult(
            status="unavailable",
            detail=f"запрос к реестру не удался: {exc.__class__.__name__}",
            source="registry", checked_at=now,
        )


# ── Оркестрация: кэш + persist ───────────────────────────────────────────────

def _fresh(cached: dict) -> bool:
    try:
        checked = dt.datetime.fromisoformat(cached["checked_at"])
    except (KeyError, ValueError):
        return False
    age = dt.datetime.now(dt.timezone.utc) - checked
    return age < dt.timedelta(hours=settings.TULPAR_CACHE_HOURS)


async def verify_permit(
    session: AsyncSession,
    shipment_id: uuid.UUID,
    doc_id: uuid.UUID,
    *,
    force: bool = False,
) -> tuple[PermitCheckResult, object]:
    """Проверить разрешительный документ; результат — в extracted.permit_check.

    Возвращает (result, row). PermitKindError — если тип документа
    не подлежит проверке. NotFoundError — пробрасывается из get_document.
    """
    row = await documents.get_document(session, shipment_id, doc_id)
    if row.kind not in VERIFIABLE_KINDS:
        raise PermitKindError(
            f"Документ типа «{row.kind}» не проверяется по реестрам разрешительных "
            f"(проверяемые типы: {', '.join(VERIFIABLE_KINDS)})"
        )

    extracted = dict(row.extracted or {})
    cached = extracted.get("permit_check") or {}
    if not force and cached and _fresh(cached):
        return (
            PermitCheckResult(
                status=cached.get("status", "unavailable"),
                detail=cached.get("detail", ""),
                source=cached.get("source", "registry"),
                checked_at=cached.get("checked_at", ""),
                cached=True,
            ),
            row,
        )

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    ok, detail = check_number_format(row.number)
    if not ok:
        result = PermitCheckResult(
            status="format_invalid", detail=detail, source="format", checked_at=now,
        )
    elif not settings.TULPAR_SEARCH_URL.strip():
        result = PermitCheckResult(
            status="format_ok",
            detail=f"{detail}; поиск по реестрам отключён (TULPAR_SEARCH_URL не задан)",
            source="format", checked_at=now,
        )
    else:
        result = await search_registry(row.number or "")

    payload = asdict(result)
    payload.pop("cached", None)
    extracted["permit_check"] = payload
    row.extracted = extracted
    await session.flush()
    return result, row
