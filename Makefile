# Lead Discovery Radar — developer entrypoints.
# Everything below is safe to run repeatedly.

SHELL := /usr/bin/env bash
BACKEND := backend
FRONTEND := frontend
COMPOSE := docker compose
UV := uv --directory $(BACKEND)
PNPM := cd $(FRONTEND) && pnpm

.DEFAULT_GOAL := help
.PHONY: help env install up down logs ps migrate revision seed-admin seed-demo-users \
        sync-sources purge-expired recompute-businesses places-smoke ai-smoke load-demo-data \
        reset-demo-data \
        reset-password \
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

install: ## Install backend and frontend dependencies
	$(UV) sync --all-groups
	$(PNPM) install

up: env ## Build and start the whole stack, waiting until it is healthy
	$(COMPOSE) up --build --detach --wait

down: ## Stop the stack (add ARGS=-v to drop the volumes too)
	$(COMPOSE) down $(ARGS)

logs: ## Follow the logs of every service
	$(COMPOSE) logs --follow

ps: ## Show the state of every service
	$(COMPOSE) ps

migrate: env ## Apply all migrations, then register the source adapters
	$(COMPOSE) run --rm api alembic upgrade head
	$(MAKE) sync-sources

revision: env ## Autogenerate a migration: make revision M="add businesses"
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(M)"

seed-admin: env ## Create or promote the bootstrap admin from ADMIN_EMAIL / ADMIN_PASSWORD
	$(COMPOSE) run --rm api python -m app.cli seed-admin

# Development only. Passwords come from DEMO_USERS_PASSWORD in .env; nothing is printed.
seed-demo-users: env ## Create reviewer@, rep1@, rep2@ and crm@example.com (development only)
	$(COMPOSE) run --rm api python -m app.cli seed-demo-users

sync-sources: env ## Upsert one `sources` row per registered adapter (idempotent)
	$(COMPOSE) run --rm api python -m app.cli sync-sources

purge-expired: env ## Drop stored source content past its retention window (keeps IDs)
	$(COMPOSE) run --rm api python -m app.cli purge-expired

# Runs the whole pipeline: discovery, resolution, website audits and scoring. No network, no key.
load-demo-data: env ## Load the fictional demo businesses, resolve, audit and score them (development only)
	$(COMPOSE) run --rm api python -m app.cli load-demo-data $(ARGS)

# Development only. Removes every review decision, suppression and opportunity, then scores
# the demo businesses again, so the queue looks exactly like a fresh `make load-demo-data`.
reset-demo-data: env ## Put the demo review state back to freshly loaded (development only)
	$(COMPOSE) run --rm api python -m app.cli reset-demo-data

# Run after the survivorship rules change: existing rows were computed by the old ones.
recompute-businesses: env ## Re-run survivorship for every business (add ARGS="--business-id ID")
	$(COMPOSE) run --rm api python -m app.cli recompute-businesses $(ARGS)

# Prompts for the password twice; it is never passed on the command line.
reset-password: env ## Reset one user's password: make reset-password EMAIL=you@example.com
	@if [ -z "$(EMAIL)" ]; then echo "usage: make reset-password EMAIL=you@example.com"; exit 2; fi
	$(COMPOSE) run --rm api python -m app.cli reset-password --email "$(EMAIL)"

# Costs real money and needs GOOGLE_PLACES_API_KEY. Set a budget alert first.
places-smoke: env ## One live Google Places call: make places-smoke ARGS="--industry plumber --city Austin --state TX"
	$(COMPOSE) run --rm api python -m app.cli places-smoke $(or $(ARGS),--industry plumber --city Austin --state TX --max 5)

# Costs real money and needs OPENAI_API_KEY + the model names. Set a monthly limit first.
ai-smoke: env ## One live OpenAI call on one demo business, stores nothing: make ai-smoke ARGS="--domain bartoncreekplumbing.invalid"
	$(COMPOSE) run --rm api python -m app.cli ai-smoke $(ARGS)

lint: ## Lint and format-check the backend
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Apply ruff's fixes and formatting to the backend
	$(UV) run ruff check . --fix
	$(UV) run ruff format .

typecheck: ## Type-check the backend
	$(UV) run mypy app tests

test: ## Run the backend test suite
	$(UV) run pytest

test-unit: ## Run only the backend unit tests (no database needed)
	$(UV) run pytest tests/unit

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
e2e: env reset-demo-data ## End-to-end smoke against http://localhost:3000 (human-run; needs the demo stack)
	$(PNPM) exec playwright install chromium
	@cd $(FRONTEND) && set -a && . ../.env && set +a && pnpm exec playwright test

check: check-backend check-frontend ## Everything CI runs

clean: ## Remove caches and build output
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(FRONTEND)/.next
	find $(BACKEND) -name '__pycache__' -type d -prune -exec rm -rf {} +
