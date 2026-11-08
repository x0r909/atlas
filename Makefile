# Everything runs through the repo venv: `make test` must not depend on what
# happens to be installed system-wide.
PY := .venv/bin/python
PIP := .venv/bin/pip
export PATH := $(CURDIR)/.venv/bin:$(PATH)

.PHONY: help install lint fmt types types-check test test-unit test-integration migrate redis-up redis-down

help:
	@echo "install           create .venv (Python 3.12) and install every package editable"
	@echo "lint              ruff + mypy over packages, services, infra, tools, tests"
	@echo "fmt               ruff format + fix"
	@echo "types             regenerate apps/terminal-web/src/lib/schema.d.ts"
	@echo "types-check       fail if schema.d.ts is stale (CI gate)"
	@echo "test              unit + contract tests (no Postgres/Redis needed)"
	@echo "test-unit         tests/unit only"
	@echo "test-integration  tests/integration (needs ATLAS_DB_DSN / ATLAS_REDIS_URL)"
	@echo "migrate           alembic upgrade head against ATLAS_DB_DSN"
	@echo "redis-up          start the dev Redis with the production eviction settings"

install:
	uv venv --python 3.12 .venv
	$(PIP) install -q -e packages/common -e packages/schemas -e services/api-gateway -e services/execution
	$(PIP) install -q pytest pytest-asyncio hypothesis respx time-machine ruff mypy alembic \
		"SQLAlchemy[asyncio]" asyncpg pydantic pydantic-settings structlog fastapi uvicorn httpx redis pyotp

# mypy is given the files that exist rather than the directories, so a slice that
# has not created (say) services/api-gateway yet still lints cleanly.
MYPY_TARGETS := $(shell find packages services infra tools -name '*.py' 2>/dev/null)

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/mypy $(if $(MYPY_TARGETS),$(MYPY_TARGETS),packages)

fmt:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

types:
	$(PY) tools/gen_types.py

types-check:
	$(PY) tools/gen_types.py --check

test:
	$(PY) -m pytest tests/unit tests/contract -q

test-unit:
	$(PY) -m pytest tests/unit -q

test-integration:
	$(PY) -m pytest tests/integration -q

migrate:
	.venv/bin/alembic -c infra/db/alembic.ini upgrade head

redis-up:
	docker run -d --name atlas-redis -p 6379:6379 --memory 384mb \
		redis:7-alpine redis-server --maxmemory 384mb --maxmemory-policy noeviction

redis-down:
	docker rm -f atlas-redis
