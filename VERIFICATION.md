# Verification — Stages 0, 1, 2

Reproducible steps for what's already been verified in this branch. Each block is
copy-pasteable; expected output is noted inline. See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
for what each stage builds and why.

## Setup (once)

```bash
cp .env.example .env
uv sync --all-packages --group dev
```

## Static checks (all stages)

```bash
uv run ruff check .                                                # → All checks passed!
uv run mypy packages/engine/src apps/api/src apps/worker/src       # → Success: no issues found
uv run pytest -v                                                   # → 30 passed
```

`apps/api/tests/test_models_db.py` and `test_auth.py` run against a real Postgres
database, not mocks. If nothing is reachable at `TEST_DATABASE_URL` (default
`postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu_test`) they **skip**
rather than fail — see "Stage 1" below to stand that database up. CI runs a disposable
Postgres service container for this on every push, so it's covered either way.

## Stage 0 — the app boots

```bash
docker compose build                 # builds api, worker, web images
docker compose up -d
docker compose ps                    # all 5 services should show healthy/running
curl -s http://localhost:8000/health # → {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000   # → 200
```

If `5432`/`6379` are already used by something else on your machine, this repo's
Postgres/Redis are on `5434`/`6380` by default (`HOST_POSTGRES_PORT` / `HOST_REDIS_PORT`
in `.env` to change) — container-to-container traffic is unaffected either way.

## Stage 1 — schema and migrations

```bash
# One-time: a dedicated DB for the DB-backed tests, separate from the app's own `revu` DB
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "CREATE DATABASE revu_test"

cd apps/api
DATABASE_URL_SYNC="postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu" \
  uv run --project .. --package api alembic upgrade head
cd ../..

docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "\dt"
# → 16 rows: the 15 domain tables (organizations, users, ..., refresh_tokens) + alembic_version
```

Now `uv run pytest apps/api/tests/test_models_db.py -v` should show 7 passing tests
(previously skipped, if `revu_test` didn't exist yet).

## Stage 2 — auth, end to end, against the real running containers

```bash
docker compose build api worker
docker compose up -d api worker
docker compose logs worker --tail 5   # → "Starting worker for 2 functions: ping, db_ping"
```

```bash
COOKIE_JAR=$(mktemp)

# Signup — creates an org + owner user, returns an access token, sets the refresh cookie
curl -s -c "$COOKIE_JAR" -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Acme Inc","email":"verify@example.com","password":"correct-horse-battery"}'
# → 201-shaped JSON: {"access_token": "...", "token_type": "bearer", "expires_in": 900, "user": {...}}

ACCESS_TOKEN="<paste access_token from above>"

# /me with the access token
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer $ACCESS_TOKEN"
# → {"id": "...", "org_id": "...", "email": "verify@example.com", "role": "owner"}

# /me with no token
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/auth/me   # → 401

# Refresh — rotates the cookie, issues a new access token
curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST http://localhost:8000/auth/refresh
# → same shape as signup, different access_token

# Logout
curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" -o /dev/null -w "%{http_code}\n" \
  -X POST http://localhost:8000/auth/logout   # → 204

rm -f "$COOKIE_JAR"
```

### Worker → Postgres wiring

```bash
uv run --package worker python - <<'PY'
import asyncio
from arq import create_pool
from arq.connections import RedisSettings

async def main():
    redis = await create_pool(RedisSettings.from_dsn("redis://localhost:6380/0"))
    job = await redis.enqueue_job("db_ping")
    print("db_ping result:", await job.result(timeout=10))

asyncio.run(main())
PY
# → db_ping result: ok
```

That confirms the worker container's own Postgres connection (set up in
`WorkerSettings.on_startup`) actually runs a query, not just that the container starts.
