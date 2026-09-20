FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /workspace

# Copy the whole workspace (workspace members reference each other via uv.sources)
COPY pyproject.toml uv.lock* ./
COPY packages/engine ./packages/engine
COPY apps/api ./apps/api

ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv
RUN uv sync --frozen --no-dev --package api || uv sync --no-dev --package api

ENV PATH="/workspace/.venv/bin:$PATH"
WORKDIR /workspace/apps/api

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
