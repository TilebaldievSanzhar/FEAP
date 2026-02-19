"""Model selection per cascade level."""

from __future__ import annotations

from feap.config import settings

# Level -> model tier mapping
# Level 0 (enrichment): lightweight
# Level 1 (section): lightweight
# Level 2 (group): lightweight/medium
# Level 3 (position): heavy — hardest classification
# Level 4 (subposition): medium
# Level 5 (subsubposition): lightweight

LEVEL_TIER: dict[int, str] = {
    0: "light",
    1: "light",
    2: "medium",
    3: "heavy",
    4: "medium",
    5: "light",
}

# Max tokens per level
LEVEL_MAX_TOKENS: dict[int, int] = {
    0: 2048,
    1: 2048,
    2: 2048,
    3: 4096,
    4: 2048,
    5: 1024,
}

_TIER_TO_SETTING = {
    "light": lambda: settings.LLM_MODEL_LIGHT,
    "medium": lambda: settings.LLM_MODEL_MEDIUM,
    "heavy": lambda: settings.LLM_MODEL_HEAVY,
}


def get_model(level: int) -> str:
    tier = LEVEL_TIER.get(level, "medium")
    return _TIER_TO_SETTING[tier]()


def get_max_tokens(level: int) -> int:
    return LEVEL_MAX_TOKENS.get(level, 2048)
