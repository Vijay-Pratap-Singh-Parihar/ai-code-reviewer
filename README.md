# revu — Agentic Code Review Platform

See [`Product_Architecture_FullStack.md`](Product_Architecture_FullStack.md) for the product design and
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the build order actually being followed (an
app-first reorder of [`Execution_Roadmap_Weeks_0_to_18.md`](Execution_Roadmap_Weeks_0_to_18.md)).

## Layout

```
apps/api/       FastAPI application (auth, routers, DB models, Alembic migrations)
apps/worker/    ARQ background worker (runs analysis jobs against the engine)
apps/web/       Next.js frontend
packages/engine/ "revu" — the review engine: indexer, context retrieval, agents, verifier
```

`apps/api` and `apps/worker` both depend on `packages/engine` as a local uv workspace member,
so engine code is written once and shared by both.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically by `uv python install 3.12`)
- [Docker](https://www.docker.com/) + Docker Compose
- Node.js 22+ (only needed for frontend work outside Docker)

## Environment

```bash
cp .env.example .env
```

Fill in at minimum `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` before the reviewer can make real LLM
calls (Stage 3+). Everything else has a working development default. Generate real secrets for
`JWT_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` before this ever runs anywhere but your laptop —
the commands to generate them are commented next to each variable in `.env.example`.

## Run everything with Docker

```bash
docker compose up --build
```

- API: http://localhost:8000/health
- Web: http://localhost:3000
- Postgres: localhost:5434, Redis: localhost:6380 (overridable via `HOST_POSTGRES_PORT` /
  `HOST_REDIS_PORT` in `.env` — defaults avoid colliding with other local Postgres/Redis on
  5432/6379; containers still talk to each other on the standard internal ports)

## Local development (without Docker, for fast iteration)

```bash
# Python (api + worker + engine)
uv sync --all-packages --group dev
uv run pytest
uv run ruff check .
uv run mypy packages/engine/src apps/api/src apps/worker/src

# Run the API directly
uv run --package api uvicorn api.main:app --reload --app-dir apps/api/src

# Run the worker directly
uv run --package worker arq worker.settings.WorkerSettings --app-dir apps/worker/src

# Frontend
cd apps/web && npm install && npm run dev
```

Postgres and Redis still need to be reachable — either run `docker compose up postgres redis`
alongside local Python processes, or point `.env` at your own instances.

## Database migrations

`api.core.config.Settings` reads `DATABASE_URL_SYNC` from `postgres:5432` — the Docker-internal
hostname, correct for containers but unreachable from the host. Running Alembic from your host
machine (against `docker compose up postgres`, exposed on `HOST_POSTGRES_PORT`) needs that
overridden:

```bash
cd apps/api
DATABASE_URL_SYNC="postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu" \
  uv run --project .. --package api alembic upgrade head
DATABASE_URL_SYNC="postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu" \
  uv run --project .. --package api alembic revision --autogenerate -m "describe the change"
```

Inside a container (or anything that resolves the `postgres` service name), no override is
needed.

## Tests that touch the database

`apps/api/tests/test_models_db.py` runs real round-trip tests (enum storage, cascades, unique
constraints) against Postgres rather than mocking the ORM. It looks for `TEST_DATABASE_URL`
(default `postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu_test`) and skips
itself — not fails — if nothing is reachable there. One-time setup against the compose Postgres:

```bash
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "CREATE DATABASE revu_test"
```

CI runs a disposable `postgres:16-alpine` service container for this instead (see
`.github/workflows/ci.yml`), so these tests also run on every push.

## Status

Build order and what's covered vs. deferred from the original roadmap: see
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). For copy-pasteable steps to independently
verify what's already built, see [`VERIFICATION.md`](VERIFICATION.md).
