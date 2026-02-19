"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from feap.api.router import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="FEAP — ТН ВЭД Classifier",
    description="Cascading LLM classifier for ЕАЭС customs tariff codes",
    version="0.1.0",
)

app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok"}
