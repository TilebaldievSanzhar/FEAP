FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir .

COPY alembic.ini migrations/ ./migrations/
COPY data/ data/
COPY scripts/ scripts/

EXPOSE 8000

CMD ["uvicorn", "feap.main:app", "--host", "0.0.0.0", "--port", "8000"]
