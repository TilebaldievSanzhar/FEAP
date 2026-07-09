"""Sensor-паттерны на текст документов: маркеры prompt-инъекций.

Документы = данные, не инструкции (dt_core_dev_plan.md §4). Экстракторы
ничего из контента не исполняют by design, но попытку инъекции надо
подсветить человеку: она означает, что документу нельзя доверять целиком.

Срабатывание НЕ останавливает извлечение — это флаг, не блокировка.
"""

from __future__ import annotations

import re

# (id, скомпилированный паттерн, человекочитаемое описание)
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "inj-ignore-en",
        re.compile(r"\b(ignore|disregard|forget)\b.{0,40}\b(instructions?|prompt)\b", re.I | re.S),
        "инструкция игнорировать предыдущие указания (EN)",
    ),
    (
        "inj-ignore-ru",
        re.compile(r"(игнорируй|забудь|отбрось).{0,40}(инструкци|указани|промпт)", re.I | re.S),
        "инструкция игнорировать предыдущие указания (RU)",
    ),
    (
        "inj-system-prompt",
        re.compile(r"(system\s+prompt|системн\w+\s+промпт)", re.I),
        "обращение к системному промпту",
    ),
    (
        "inj-reveal",
        re.compile(r"(выведи|покажи|раскрой|reveal|print)\s.{0,30}(промпт|prompt|инструкци)", re.I),
        "попытка выведать промпт/инструкции",
    ),
    (
        "inj-roleplay",
        re.compile(r"(ты\s+теперь|you\s+are\s+now|act\s+as\s+if|притворись)", re.I),
        "попытка смены роли модели",
    ),
    (
        "inj-new-instructions",
        re.compile(r"(новые?\s+инструкци|new\s+instructions?|updated\s+instructions?)", re.I),
        "объявление «новых инструкций» внутри документа",
    ),
    (
        "inj-jailbreak",
        re.compile(r"\b(jailbreak|DAN\s+mode|do\s+anything\s+now)\b", re.I),
        "известный jailbreak-маркер",
    ),
    (
        "inj-exfiltrate",
        re.compile(r"(api[-_\s]?key|секретн\w+\s+ключ|credentials?|токен\s+доступа)", re.I),
        "упоминание ключей/кредов — возможная попытка эксфильтрации",
    ),
]


def scan_text(text: str) -> list[str]:
    """Вернуть предупреждения по инъекционным маркерам в тексте документа.

    Payload не цитируем (антипаттерн verbose denial) — только id и описание.
    """
    warnings: list[str] = []
    for pattern_id, pattern, label in _PATTERNS:
        if pattern.search(text):
            warnings.append(
                f"[{pattern_id}] Текст содержит {label}. Содержимое документа — "
                "данные, инструкции из него не исполняются; проверьте документ вручную."
            )
    return warnings
