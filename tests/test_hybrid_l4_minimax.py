"""Test hybrid config: gemini-3.1-flash-lite (all levels) + minimax-m2.7 (L4 only).

Tests 3 cases that consistently fail at L4 in v16 benchmark.
Run: venv/Scripts/python.exe tests/test_hybrid_l4_minimax.py
"""

import asyncio
import json
import logging
import os
import sys
import time

# --- Override env BEFORE importing feap ---
os.environ["MODEL_LIGHT"] = "deepseek/deepseek-v3.2"
os.environ["MODEL_MEDIUM"] = "deepseek/deepseek-v3.2"
os.environ["MODEL_HEAVY"] = "deepseek/deepseek-v3.2"
os.environ["LEVEL4_MODEL"] = "minimax/minimax-m2.7"
# Keep existing DATABASE_URL and OPENROUTER_API_KEY from .env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("test_hybrid")

# Suppress noisy logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

# Test cases that fail at L4 in v16
TEST_CASES = [
    {
        "id": "BENCH-H18",
        "description": (
            "Квадрокоптер (дрон) с камерой 4K, время полёта 30 мин, "
            "дальность 10 км, GPS, автовозврат, масса 249 г. "
            "Для аэросъёмки и видеографии."
        ),
        "expected_subheading": "880610",
        "expected_heading": "8806",
        "note": "249g drone — must be <=2000kg (880610), NOT >2000kg (880621)",
    },
    {
        "id": "BENCH-H19",
        "description": (
            "Пикап (pickup truck) с двойной кабиной, двигатель дизельный 2.4 л, "
            "полный привод, грузоподъёмность платформы 1 тонна. "
            "Масса снаряжённого ТС 2100 кг. Новый."
        ),
        "expected_subheading": "870423",
        "expected_heading": "8704",
        "note": "Total mass ~3.1t — must be <=5t (870423), NOT >5t (870421)",
    },
    {
        "id": "BENCH-M22",
        "description": (
            "Свитер мужской трикотажный машинной вязки, 100% шерсть мериноса, "
            "круглая горловина, длинный рукав, размер L."
        ),
        "expected_subheading": "611019",
        "expected_heading": "6110",
        "note": "Merino sweater L ~400-500g — must be <600g (611019), NOT >=600g (611011)",
    },
]


async def run_test(case: dict) -> dict:
    """Run full cascade for a single test case."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from feap.cascade.orchestrator import CascadeOrchestrator
    from feap.config import settings
    from feap.llm.models import resolve_model

    # Log which models will be used
    logger.info("=== %s ===", case["id"])
    logger.info("Description: %s", case["description"][:80])
    logger.info("Expected subheading: %s", case["expected_subheading"])
    logger.info("Note: %s", case["note"])
    for lvl in [0, 1, 2, 3, 4, 5]:
        logger.info("  Level %d model: %s", lvl, resolve_model(lvl))

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    orchestrator = CascadeOrchestrator()

    start = time.time()
    async with async_session() as db:
        response = await orchestrator.classify(case["description"], db)
    elapsed = time.time() - start

    result = {
        "id": case["id"],
        "status": response.status,
        "elapsed_s": round(elapsed, 1),
        "expected_heading": case["expected_heading"],
        "expected_subheading": case["expected_subheading"],
    }

    if response.status == "completed" and response.result:
        chain = response.result.classification_chain
        heading = chain.position.code if chain.position else "?"
        subheading = chain.subposition.code if chain.subposition else "?"
        code_10 = response.result.final_code

        result["heading"] = heading
        result["subheading"] = subheading
        result["code_10"] = code_10
        result["heading_match"] = heading == case["expected_heading"]
        result["subheading_match"] = subheading == case["expected_subheading"]

        status_icon = "OK" if result["subheading_match"] else "FAIL"
        logger.info(
            "[%s] %s | heading=%s (exp=%s) | subheading=%s (exp=%s) | code=%s | %.1fs",
            status_icon,
            case["id"],
            heading,
            case["expected_heading"],
            subheading,
            case["expected_subheading"],
            code_10,
            elapsed,
        )

        # Log L4 decision details
        if chain.subposition:
            logger.info("  L4 reasoning: %s", chain.subposition.reasoning[:200])
    elif response.status == "needs_input":
        result["needs_input"] = True
        logger.info(
            "[NEEDS_INPUT] %s | partial=%s | questions=%s | %.1fs",
            case["id"],
            response.partial.partial_code if response.partial else "?",
            response.partial.questions if response.partial else [],
            elapsed,
        )
    else:
        result["error"] = response.error
        logger.info("[ERROR] %s | %s | %.1fs", case["id"], response.error, elapsed)

    await engine.dispose()
    return result


async def main():
    logger.info("=" * 70)
    logger.info("Hybrid L4 test: flash-lite (all) + minimax-m2.7 (L4)")
    logger.info("=" * 70)

    results = []
    for case in TEST_CASES:
        try:
            r = await run_test(case)
            results.append(r)
        except Exception as e:
            logger.exception("Test %s failed with exception", case["id"])
            results.append({"id": case["id"], "error": str(e)})

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    total = len(results)
    heading_ok = sum(1 for r in results if r.get("heading_match"))
    subheading_ok = sum(1 for r in results if r.get("subheading_match"))
    logger.info("Heading correct:    %d/%d", heading_ok, total)
    logger.info("Subheading correct: %d/%d (this is what we're testing)", subheading_ok, total)

    for r in results:
        status = "OK" if r.get("subheading_match") else "FAIL"
        logger.info(
            "  %s %s: subheading=%s (exp=%s) | %.1fs",
            status,
            r["id"],
            r.get("subheading", "?"),
            r.get("expected_subheading", "?"),
            r.get("elapsed_s", 0),
        )


if __name__ == "__main__":
    asyncio.run(main())