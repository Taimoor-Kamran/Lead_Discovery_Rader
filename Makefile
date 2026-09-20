# Lead Discovery Radar — developer entrypoints.
# Everything below is safe to run repeatedly.

SHELL := /usr/bin/env bash
BACKEND := backend
FRONTEND := frontend
UV := uv --directory $(BACKEND)
PNPM := cd $(FRONTEND) && pnpm

# Development stack by default. `PROD=1` points every compose command at production mode
# on this machine (docker-compose.prod.yml + .env.prod, its own project and volumes), e.g.
# `make backup PROD=1`, `make seed-admin PROD=1`, `make migrate PROD=1`.
ifeq ($(PROD),1)
COMPOSE := docker compose -p radar-prod --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml
ENV_DEP := env-prod
else
COMPOSE := docker compose
ENV_DEP := env
endif

.DEFAULT_GOAL := help
.PHONY: help env env-prod install up down logs ps migrate revision seed-admin seed-demo-users \
        sync-sources purge-expired recompute-businesses places-smoke ai-smoke load-demo-data \
        reset-demo-data \
        reset-password crm-check crm-bootstrap-airtable \
        prod-up prod-down prod-logs prod-ps backup restore backup-verify audit \
        lint format typecheck test test-unit check check-backend check-frontend \
        frontend-install frontend-lint frontend-typecheck frontend-test \
        api-types api-types-check e2e clean FORCE

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it does not exist yet
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "created .env from .env.example — set JWT_SECRET and ADMIN_EMAIL before using it for real"; \
	fi

# Never copied automatically: production secrets are typed in by a human on purpose.
env-prod:
	@if [ ! -f .env.prod ]; then \
		echo "missing .env.prod — copy .env.prod.example to .env.prod and fill in the real values (see docs/operations.md)"; \
		exit 2; \
	fi

install: ## Install backend and frontend dependencies
	$(UV) sync --all-groups
	$(PNPM) install

up: $(ENV_DEP) ## Build and start the whole stack, waiting until it is healthy
	$(COMPOSE) up --build --detach --wait

down: ## Stop the stack (add ARGS=-v to drop the volumes too)
	$(COMPOSE) down $(ARGS)

logs: ## Follow the logs of every service
	$(COMPOSE) logs --follow

ps: ## Show the state of every service
	$(COMPOSE) ps

migrate: $(ENV_DEP) ## Apply all migrations, then register the source adapters
	$(COMPOSE) run --rm api alembic upgrade head
	$(MAKE) sync-sources

revision: $(ENV_DEP) ## Autogenerate a migration: make revision M="add businesses"
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(M)"

seed-admin: $(ENV_DEP) ## Create or promote the bootstrap admin from ADMIN_EMAIL / ADMIN_PASSWORD
	$(COMPOSE) run --rm api python -m app.cli seed-admin

# Development only. Passwords come from DEMO_USERS_PASSWORD in .env; nothing is printed.
seed-demo-users: $(ENV_DEP) ## Create reviewer@, rep1@, rep2@ and crm@example.com (development only)
	$(COMPOSE) run --rm api python -m app.cli seed-demo-users

sync-sources: $(ENV_DEP) ## Upsert one `sources` row per registered adapter (idempotent)
	$(COMPOSE) run --rm api python -m app.cli sync-sources

purge-expired: $(ENV_DEP) ## Drop stored source content past its retention window (keeps IDs)
	$(COMPOSE) run --rm api python -m app.cli purge-expired

# Runs the whole pipeline: discovery, resolution, website audits and scoring. No network, no key.
load-demo-data: $(ENV_DEP) ## Load the fictional demo businesses, resolve, audit and score them (development only)
	$(COMPOSE) run --rm api python -m app.cli load-demo-data $(ARGS)

# Development only. Removes every review decision, suppression and opportunity, then scores
# the demo businesses again, so the queue looks exactly like a fresh `make load-demo-data`.
reset-demo-data: $(ENV_DEP) ## Put the demo review state back to freshly loaded (development only)
	$(COMPOSE) run --rm api python -m app.cli reset-demo-data

# Run after the survivorship rules change: existing rows were computed by the old ones.
recompute-businesses: $(ENV_DEP) ## Re-run survivorship for every business (add ARGS="--business-id ID")
	$(COMPOSE) run --rm api python -m app.cli recompute-businesses $(ARGS)

# Prompts for the password twice; it is never passed on the command line.
reset-password: $(ENV_DEP) ## Reset one user's password: make reset-password EMAIL=you@example.com
	@if [ -z "$(EMAIL)" ]; then echo "usage: make reset-password EMAIL=you@example.com"; exit 2; fi
	$(COMPOSE) run --rm api python -m app.cli reset-password --email "$(EMAIL)"

# Costs real money and needs GOOGLE_PLACES_API_KEY. Set a budget alert first.
places-smoke: $(ENV_DEP) ## One live Google Places call: make places-smoke ARGS="--industry plumber --city Austin --state TX"
	$(COMPOSE) run --rm api python -m app.cli places-smoke $(or $(ARGS),--industry plumber --city Austin --state TX --max 5)

# Costs real money and needs OPENAI_API_KEY + the model names. Set a monthly limit first.
ai-smoke: $(ENV_DEP) ## One live OpenAI call on one demo business, stores nothing: make ai-smoke ARGS="--domain bartoncreekplumbing.invalid"
	$(COMPOSE) run --rm api python -m app.cli ai-smoke $(ARGS)

# Reads the CRM destination's setup and prints OK / missing per field. Writes nothing.
# For Airtable the token needs schema.bases:read.
crm-check: $(ENV_DEP) ## Check the CRM destination (token, base, table, every mapped field)
	$(COMPOSE) run --rm api python -m app.cli crm-check

# Creates the Airtable Leads table with every field. Needs schema.bases:write on the token.
# Refuses if the table already exists.
crm-bootstrap-airtable: $(ENV_DEP) ## Create the Airtable Leads table from the field map (optional)
	$(COMPOSE) run --rm api python -m app.cli crm-bootstrap-airtable

# --- Production mode on this machine (spec v0.8.0; see docs/operations.md) -----------------
# Everything is published on 127.0.0.1 only; PostgreSQL and Redis are not published at all.
# Data lives in the radar-prod_postgres-data-prod / radar-prod_redis-data-prod volumes,
# dumps in ./backups and logs in ./logs.
prod-up: ## Build and start production mode from .env.prod (127.0.0.1 only)
	@$(MAKE) --no-print-directory up PROD=1

prod-down: ## Stop production mode (volumes are kept; add ARGS=-v to drop them — think twice)
	@$(MAKE) --no-print-directory down PROD=1

prod-logs: ## Follow the production logs
	@$(MAKE) --no-print-directory logs PROD=1

prod-ps: ## Show the state of the production services
	@$(MAKE) --no-print-directory ps PROD=1

# --- Backups (add PROD=1 for production mode) -----------------------------------------------
backup: $(ENV_DEP) ## pg_dump -Fc into ./backups/radar-YYYYMMDD-HHMMSS.dump, keep the newest BACKUP_KEEP
	@mkdir -p backups
	$(COMPOSE) run --rm api python -m app.cli backup

# Replaces the whole database. Asks for a typed confirmation, stops api + worker, restores,
# migrates, and starts them again.
restore: $(ENV_DEP) ## Restore a dump in place: make restore FILE=backups/radar-....dump
	@if [ -z "$(FILE)" ]; then echo "usage: make restore FILE=backups/radar-YYYYMMDD-HHMMSS.dump"; exit 2; fi
	@if [ ! -f "$(FILE)" ]; then echo "$(FILE) does not exist"; exit 2; fi
	@echo "This REPLACES every table in the database with the contents of $(notdir $(FILE))."
	@read -r -p "Type 'RESTORE $(notdir $(FILE))' to continue: " answer; \
		if [ "$$answer" != "RESTORE $(notdir $(FILE))" ]; then echo "aborted, nothing was changed"; exit 2; fi
	$(COMPOSE) stop api worker
	$(COMPOSE) run --rm api python -m app.cli restore --file /app/backups/$(notdir $(FILE)) --yes
	$(COMPOSE) run --rm api alembic upgrade head
	$(COMPOSE) up -d api worker
	@echo "Restored $(notdir $(FILE)); api and worker are back up."

backup-verify: $(ENV_DEP) ## Restore the newest dump into a throw-away database, check it, drop it
	$(COMPOSE) run --rm api python -m app.cli backup-verify $(ARGS)

# Needs the network (advisory databases), so it is not part of `make check`; CI runs it.
audit: ## Dependency audit: pip-audit (backend) + pnpm audit --prod (frontend); fails on high/critical
	$(UV) run pip-audit --progress-spinner off
	$(PNPM) audit --prod --audit-level=high

lint: ## Lint and format-check the backend
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Apply ruff's fixes and formatting to the backend
	$(UV) run ruff check . --fix
	$(UV) run ruff format .

typecheck: ## Type-check the backend
	$(UV) run mypy app tests

# `-n auto`: one pytest worker per CPU, each with its own database on one shared
# PostgreSQL container (tests/conftest.py). `TEST_ARGS=-n0` runs serially.
TEST_ARGS ?= -n auto
test: ## Run the backend test suite (parallel; TEST_ARGS=-n0 for serial)
	$(UV) run pytest $(TEST_ARGS)

test-unit: ## Run only the backend unit tests (no database needed)
	$(UV) run pytest tests/unit $(TEST_ARGS)

frontend-install: ## Install frontend dependencies
	$(PNPM) install --frozen-lockfile

frontend-lint: ## Lint the frontend
	$(PNPM) run lint

frontend-typecheck: ## Type-check the frontend
	$(PNPM) run typecheck

frontend-test: ## Run the frontend test suite
	$(PNPM) run test

check-backend: lint typecheck test ## Backend lint + types + tests

# The committed `frontend/src/lib/api-types.ts` is generated from the backend's OpenAPI
# document. `api-types-check` fails when it is out of date; run `make api-types` then.
API_TYPES := $(FRONTEND)/src/lib/api-types.ts
OPENAPI_JSON := $(FRONTEND)/.openapi.json

$(OPENAPI_JSON): FORCE
	@$(UV) run python -m app.openapi_export $(abspath $@) >/dev/null

api-types: $(OPENAPI_JSON) ## Regenerate frontend/src/lib/api-types.ts from the backend's OpenAPI document
	$(PNPM) exec openapi-typescript .openapi.json -o src/lib/api-types.ts
	@rm -f $(OPENAPI_JSON)

api-types-check: $(OPENAPI_JSON) ## Fail if the committed API types are out of date
	@cd $(FRONTEND) && pnpm exec openapi-typescript .openapi.json -o .api-types.check.ts >/dev/null \
		&& if ! diff -q .api-types.check.ts src/lib/api-types.ts >/dev/null; then \
			echo "frontend/src/lib/api-types.ts is out of date: run 'make api-types' and commit it"; \
			diff .api-types.check.ts src/lib/api-types.ts | head -40; rm -f .api-types.check.ts .openapi.json; exit 1; fi; \
		rm -f .api-types.check.ts .openapi.json
	@echo "api types are up to date"

FORCE:

check-frontend: api-types-check frontend-lint frontend-typecheck frontend-test ## Frontend types drift + lint + types + tests

# Needs the compose stack up with demo data and demo users (see README "Reviewing leads").
# Resets the demo review state first so the run never depends on what a human clicked.
# Playwright's Chromium is downloaded on first run. Not part of `make check`.
e2e: env ## End-to-end smoke against http://localhost:3000 (human-run; needs the demo stack)
	@if ! grep -qE '^CRM_DESTINATION=fake\s*$$' .env; then \
		echo "make e2e needs CRM_DESTINATION=fake in .env (the in-database stand-in the CRM steps use)."; \
		echo "Set it, run 'make up' so the api and worker pick it up, then run 'make e2e' again."; \
		exit 2; \
	fi
	@$(MAKE) --no-print-directory reset-demo-data
	$(PNPM) exec playwright install chromium
	@cd $(FRONTEND) && set -a && . ../.env && set +a && pnpm exec playwright test

check: check-backend check-frontend ## Everything CI runs

clean: ## Remove caches and build output
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(FRONTEND)/.next
	find $(BACKEND) -name '__pycache__' -type d -prune -exec rm -rf {} +
