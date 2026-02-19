"""Model selection per cascade level."""

from __future__ import annotations

# Level -> model mapping
# Level 0 (enrichment): lightweight
# Level 1 (section): lightweight
# Level 2 (group): lightweight/medium
# Level 3 (position): heavy — hardest classification
# Level 4 (subposition): medium
# Level 5 (subsubposition): lightweight

LEVEL_MODELS: dict[int, str] = {
    0: "claude-sonnet-4-6",
    1: "claude-sonnet-4-6",
    2: "claude-sonnet-4-6",
    3: "claude-opus-4-6",
    4: "claude-sonnet-4-6",
    5: "claude-sonnet-4-6",
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


def get_model(level: int) -> str:
    return LEVEL_MODELS.get(level, "claude-sonnet-4-6")


def get_max_tokens(level: int) -> int:
    return LEVEL_MAX_TOKENS.get(level, 2048)
