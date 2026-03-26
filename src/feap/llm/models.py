"""Model selection per cascade level — supports multiple providers and per-level overrides."""

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
    99: "light",  # NTM verification (dual matching)
}

# Max output tokens per level (excludes thinking tokens)
LEVEL_MAX_TOKENS: dict[int, int] = {
    0: 4096,
    1: 4096,
    2: 4096,
    3: 8192,
    4: 4096,
    5: 2048,
    99: 2048,  # NTM verification
}

# Thinking budget per level (Gemini only, tokens for internal reasoning).
# With SGR, schema fields guide reasoning explicitly, so less native thinking needed.
LEVEL_THINKING_BUDGET: dict[int, int] = {
    0: 0,      # L0 enrichment — simple, SGR sufficient
    1: 256,    # L1 section — light analysis
    2: 256,    # L2 group — light analysis
    3: 512,    # L3 position — hardest level, SGR + some thinking
    4: 256,    # L4 subposition — thresholds, SGR helps
    5: 0,      # L5 final code — SGR sufficient
    99: 0,     # NTM verification
}

# Per-level temperature. SGR levels (L4/L5) use 0.0 for determinism.
# Upper levels (L0-L3) use None → Gemini API default (not constrained).
LEVEL_TEMPERATURE: dict[int, float | None] = {
    0: 0.0,    # L0 enrichment — deterministic
    1: 0.0,    # L1 section — deterministic + backtrack context prevents loops
    2: 0.0,    # L2 group — deterministic
    3: 0.0,    # L3 position — deterministic
    4: 0.0,    # L4 subposition — SGR determinism
    5: 0.0,    # L5 final code — SGR determinism
    99: 0.0,   # NTM verification
}

# Per-level override env var mapping
_LEVEL_MODEL_SETTINGS: dict[int, str] = {
    0: "LEVEL0_MODEL",
    1: "LEVEL1_MODEL",
    2: "LEVEL2_MODEL",
    3: "LEVEL3_MODEL",
    4: "LEVEL4_MODEL",
    5: "LEVEL5_MODEL",
    99: "LEVEL99_MODEL",
}


def resolve_model(level: int) -> tuple[str, str]:
    """Return (provider, model) for a given cascade level.

    Checks LEVEL{N}_MODEL first (format: "provider/model").
    Falls back to LLM_PROVIDER + tier-based model selection.
    """
    override = getattr(settings, _LEVEL_MODEL_SETTINGS.get(level, ""), "")
    if override and "/" in override:
        provider, model = override.split("/", 1)
        return provider, model

    # Fallback to tier-based selection
    provider = settings.LLM_PROVIDER
    tier = LEVEL_TIER.get(level, "medium")

    if provider == "google":
        model = {
            "light": settings.GEMINI_MODEL_LIGHT,
            "medium": settings.GEMINI_MODEL_MEDIUM,
            "heavy": settings.GEMINI_MODEL_HEAVY,
        }[tier]
    else:
        model = {
            "light": settings.ANTHROPIC_MODEL_LIGHT,
            "medium": settings.ANTHROPIC_MODEL_MEDIUM,
            "heavy": settings.ANTHROPIC_MODEL_HEAVY,
        }[tier]

    return provider, model


def get_model(level: int) -> str:
    """Return the model name for a given cascade level (legacy compat)."""
    _, model = resolve_model(level)
    return model


def get_max_tokens(level: int) -> int:
    return LEVEL_MAX_TOKENS.get(level, 2048)


def get_temperature(level: int) -> float | None:
    """Return temperature for a given level. None means use API default."""
    return LEVEL_TEMPERATURE.get(level)


def get_thinking_budget(level: int) -> int:
    """Return thinking budget for a given level.

    If GEMINI_THINKING_BUDGET >= 0 in settings, use it as global override.
    Otherwise use per-level defaults from LEVEL_THINKING_BUDGET.
    """
    if settings.GEMINI_THINKING_BUDGET >= 0:
        return settings.GEMINI_THINKING_BUDGET
    return LEVEL_THINKING_BUDGET.get(level, 256)
