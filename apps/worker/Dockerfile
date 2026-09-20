FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /workspace

COPY pyproject.toml uv.lock* ./
COPY packages/engine ./packages/engine
COPY packages/db ./packages/db
COPY apps/worker ./apps/worker

ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv
RUN uv sync --frozen --no-dev --package worker || uv sync --no-dev --package worker

ENV PATH="/workspace/.venv/bin:$PATH"
WORKDIR /workspace/apps/worker

CMD ["arq", "worker.settings.WorkerSettings"]
