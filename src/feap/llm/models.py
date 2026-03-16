"""Model selection per cascade level — supports multiple providers."""

from __future__ import annotations

from feap.config import settings

# Level -> model tier mapping
LEVEL_TIER: dict[int, str] = {
    0: "light",   # Enrichment
    1: "light",   # Section
    2: "medium",  # Group
    3: "heavy",   # Position (2-step process)
    4: "medium",  # Subposition
    5: "light",   # Subsubposition
    99: "light",  # NTM verification (dual matching)
}

# Max tokens per level
LEVEL_MAX_TOKENS: dict[int, int] = {
    0: 2048,
    1: 2048,
    2: 2048,
    3: 4096,
    4: 2048,
    5: 1024,
    99: 2048,  # NTM verification
}


def get_model(level: int) -> str:
    """Return the model name for a given cascade level and active provider."""
    tier = LEVEL_TIER.get(level, "medium")

    if settings.LLM_PROVIDER == "google":
        return {
            "light": settings.GEMINI_MODEL_LIGHT,
            "medium": settings.GEMINI_MODEL_MEDIUM,
            "heavy": settings.GEMINI_MODEL_HEAVY,
        }[tier]

    return {
        "light": settings.ANTHROPIC_MODEL_LIGHT,
        "medium": settings.ANTHROPIC_MODEL_MEDIUM,
        "heavy": settings.ANTHROPIC_MODEL_HEAVY,
    }[tier]


def get_max_tokens(level: int) -> int:
    return LEVEL_MAX_TOKENS.get(level, 2048)
