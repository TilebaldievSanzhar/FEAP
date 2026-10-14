"""Model selection per cascade level — tier-based with per-level overrides."""

from __future__ import annotations

from feap.config import settings

# Level -> model tier mapping (fallback when no per-level override)
LEVEL_TIER: dict[int, str] = {
    0: "light",   # Enrichment
    1: "light",   # Section
    2: "medium",  # Group
    3: "heavy",   # Position (2-step process)
    4: "medium",  # Subposition
    5: "light",   # Subsubposition
    90: "light",  # DT: типизация документа (routing)
    91: "medium", # DT: извлечение полей документа
    92: "light",  # DT: кросс-сверка — нечёткое сопоставление сущностей
    93: "medium", # DT: генератор описания графы 31
    94: "medium", # DT: риск-советник
    95: "medium", # DT: findings-fixer
    97: "medium", # Фото/скриншот товара → карточка (vision)
    98: "medium", # OCR raw-text → ProductCard[] structurer
    99: "light",  # NTM verification (dual matching)
}

# Max output tokens per level (excludes thinking tokens)
LEVEL_MAX_TOKENS: dict[int, int] = {
    0: 4096,
    1: 4096,
    2: 4096,
    3: 8192,
    4: 4096,
    5: 4096,
    90: 2048,  # doc typing — короткий routing-ответ
    91: 8192,  # извлечение — инвойс может содержать десятки строк
    92: 4096,
    93: 4096,
    94: 8192,
    95: 8192,
    97: 4096,  # карточка товара по изображению
    98: 8192,  # OCR structurer — invoices can list many products
    99: 2048,  # NTM verification
}

# Per-level temperature. 0.0 for determinism across all levels.
LEVEL_TEMPERATURE: dict[int, float | None] = {
    0: 0.0,
    1: 0.0,
    2: 0.0,
    3: 0.0,
    4: 0.0,
    5: 0.0,
    90: 0.0,
    91: 0.0,
    92: 0.0,
    93: 0.0,
    94: 0.0,
    95: 0.0,
    97: 0.0,
    98: 0.0,
    99: 0.0,
}

# Per-level override env var mapping
_LEVEL_MODEL_SETTINGS: dict[int, str] = {
    0: "LEVEL0_MODEL",
    1: "LEVEL1_MODEL",
    2: "LEVEL2_MODEL",
    3: "LEVEL3_MODEL",
    4: "LEVEL4_MODEL",
    5: "LEVEL5_MODEL",
    90: "LEVEL90_MODEL",
    91: "LEVEL91_MODEL",
    92: "LEVEL92_MODEL",
    93: "LEVEL93_MODEL",
    94: "LEVEL94_MODEL",
    95: "LEVEL95_MODEL",
    97: "LEVEL97_MODEL",
    98: "LEVEL98_MODEL",
    99: "LEVEL99_MODEL",
}

_TIER_TO_SETTING = {
    "light": "MODEL_LIGHT",
    "medium": "MODEL_MEDIUM",
    "heavy": "MODEL_HEAVY",
}


def resolve_model(level: int) -> str:
    """Return the OpenRouter model ID for a given cascade level.

    Checks LEVEL{N}_MODEL first (e.g. "google/gemini-2.5-pro").
    Falls back to tier-based selection (MODEL_LIGHT / MODEL_MEDIUM / MODEL_HEAVY).
    """
    override = getattr(settings, _LEVEL_MODEL_SETTINGS.get(level, ""), "")
    if override:
        return override

    tier = LEVEL_TIER.get(level, "medium")
    return getattr(settings, _TIER_TO_SETTING[tier])


def get_model(level: int) -> str:
    """Return the model name for a given cascade level."""
    return resolve_model(level)


def get_max_tokens(level: int) -> int:
    return LEVEL_MAX_TOKENS.get(level, 2048)


def get_temperature(level: int) -> float | None:
    """Return temperature for a given level. None means use API default."""
    return LEVEL_TEMPERATURE.get(level)
