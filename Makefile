# Lead Discovery Radar — developer entrypoints.
# Everything below is safe to run repeatedly.

SHELL := /usr/bin/env bash
BACKEND := backend
FRONTEND := frontend
COMPOSE := docker compose
UV := uv --directory $(BACKEND)
PNPM := cd $(FRONTEND) && pnpm

.DEFAULT_GOAL := help
.PHONY: help env install up down logs ps migrate revision seed-admin \
        sync-sources purge-expired places-smoke \
        lint format typecheck test test-unit check check-backend check-frontend \
        frontend-install frontend-lint frontend-typecheck frontend-test clean

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

sync-sources: env ## Upsert one `sources` row per registered adapter (idempotent)
	$(COMPOSE) run --rm api python -m app.cli sync-sources

purge-expired: env ## Drop stored source content past its retention window (keeps IDs)
	$(COMPOSE) run --rm api python -m app.cli purge-expired

# Costs real money and needs GOOGLE_PLACES_API_KEY. Set a budget alert first.
places-smoke: env ## One live Google Places call: make places-smoke ARGS="--industry plumber --city Austin --state TX"
	$(COMPOSE) run --rm api python -m app.cli places-smoke $(or $(ARGS),--industry plumber --city Austin --state TX --max 5)

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

check-frontend: frontend-lint frontend-typecheck frontend-test ## Frontend lint + types + tests

check: check-backend check-frontend ## Everything CI runs

clean: ## Remove caches and build output
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(FRONTEND)/.next
	find $(BACKEND) -name '__pycache__' -type d -prune -exec rm -rf {} +
