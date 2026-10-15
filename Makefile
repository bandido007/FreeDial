# ════════════════════════════════════════════════════════════════════════════
#  Developer entry points. `make` or `make help` lists them.
#  Every target works from a fresh clone; CI runs the same targets.
# ════════════════════════════════════════════════════════════════════════════

.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON        ?= python3.12
VENV          ?= .venv
BIN           := $(VENV)/bin
PY            := $(BIN)/python
MANAGE        := $(PY) manage.py
SETTINGS      ?= config.settings.development
TEST_SETTINGS ?= config.settings.testing
COMPOSE       ?= docker compose
PORT          ?= 8000

export DJANGO_SETTINGS_MODULE ?= $(SETTINGS)

# ── Setup ───────────────────────────────────────────────────────────────────
.PHONY: help init venv deps deps-upgrade env

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\n"} \
	     /^[a-zA-Z_-]+:.*?##/ { sub(/ *# module:[a-z]+$$/, "", $$2); printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } \
	     /^## / { printf "\n\033[1m%s\033[0m\n", substr($$0, 4) }' $(MAKEFILE_LIST)
	@echo

## Setup
init: venv env ## First run: virtualenv, dependencies, .env, migrate, seed
	$(MAKE) migrate seed
	@echo
	@echo "Ready. Next: 'make run' (API on :$(PORT)) — sign in with DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD from .env"

venv: ## Create the virtualenv and install development requirements
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet --require-hashes -r requirements/development.txt
	$(BIN)/pre-commit install >/dev/null 2>&1 || true

env: ## Write .env from .env.example with fresh keys (never overwrites)
	@test -f .env && echo ".env exists — leaving it alone" || $(PY) scripts/write_env.py

deps: ## Recompile requirements/*.txt (pinned + hashed) from the *.in files
	$(BIN)/uv pip compile --generate-hashes -q requirements/base.in        -o requirements/base.txt
	$(BIN)/uv pip compile --generate-hashes -q requirements/testing.in     -o requirements/testing.txt
	$(BIN)/uv pip compile --generate-hashes -q requirements/production.in  -o requirements/production.txt
	$(BIN)/uv pip compile --generate-hashes -q requirements/development.in -o requirements/development.txt
	$(BIN)/uv pip compile --generate-hashes -q requirements/ldap.in        -o requirements/ldap.txt

deps-upgrade: ## Same as deps, but move every pin to the newest allowed version
	$(BIN)/uv pip compile --generate-hashes --upgrade -q requirements/base.in        -o requirements/base.txt
	$(BIN)/uv pip compile --generate-hashes --upgrade -q requirements/testing.in     -o requirements/testing.txt
	$(BIN)/uv pip compile --generate-hashes --upgrade -q requirements/production.in  -o requirements/production.txt
	$(BIN)/uv pip compile --generate-hashes --upgrade -q requirements/development.in -o requirements/development.txt
	$(BIN)/uv pip compile --generate-hashes --upgrade -q requirements/ldap.in        -o requirements/ldap.txt

# ── Run ─────────────────────────────────────────────────────────────────────
.PHONY: run worker beat shell up down logs

## Run
run: ## Development server on :$(PORT)
	$(MANAGE) runserver 0.0.0.0:$(PORT)

worker: ## Celery worker
	$(BIN)/celery -A config worker --loglevel=info

beat: ## Celery beat (periodic tasks)
	$(BIN)/celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler

shell: ## Django shell (IPython when installed)
	$(MANAGE) shell

up: ## Start PostgreSQL, Redis and Mailpit in Docker
	$(COMPOSE) up -d postgres redis mailpit

down: ## Stop the Docker services
	$(COMPOSE) down

logs: ## Follow the Docker services' logs
	$(COMPOSE) logs -f

# ── Database ────────────────────────────────────────────────────────────────
.PHONY: migrate migrations seed schema workflow-docs

## Database & catalogues
migrate: ## Apply migrations
	$(MANAGE) migrate --no-input

migrations: ## Write migrations for every app (make migrations app=<label> for one)
	$(MANAGE) makemigrations $(app)

seed: ## Seed the permission catalogue, notification templates, workflows and the first administrator
	$(MANAGE) seed


schema: ## Regenerate docs/schema.graphql from the live GraphQL schema
	$(MANAGE) export_schema

# ── Quality ─────────────────────────────────────────────────────────────────
.PHONY: test test-fast cov lint format typecheck check audit ci validate-fe

## Quality
test: ## Run the test suite
	DJANGO_SETTINGS_MODULE=$(TEST_SETTINGS) $(PY) -m pytest

test-fast: ## Tests without coverage, stop at the first failure
	DJANGO_SETTINGS_MODULE=$(TEST_SETTINGS) $(PY) -m pytest -x -q --no-cov

cov: ## Tests with an HTML coverage report in htmlcov/
	DJANGO_SETTINGS_MODULE=$(TEST_SETTINGS) $(PY) -m pytest --cov-report=html --cov-report=term
	@echo "open htmlcov/index.html"

format-web: ## Prettier for the pages' CSS and JS (never Django templates: it splits {% %} tags across lines)
	npx --yes prettier@3.3.3 --write static/algodial apps/agentdesk/static/agentdesk/app.css \
		apps/agentdesk/static/agentdesk/app.js apps/supervisor/static/supervisor

lint: ## Ruff (lint) + import-linter (architecture rules)
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	$(BIN)/lint-imports

format: ## Format and auto-fix with ruff
	$(BIN)/ruff format .
	$(BIN)/ruff check . --fix

typecheck: ## mypy with django-stubs
	$(BIN)/mypy apps config brain contracts aiworker  # project: + brain, contracts, aiworker

check: ## Django system checks + production settings dry run + generated files current
	$(MANAGE) check
	DJANGO_SETTINGS_MODULE=$(TEST_SETTINGS) $(MANAGE) export_schema --check
	$(MANAGE) makemigrations --check --dry-run

audit: ## Known-vulnerability scan of the pinned dependencies
	$(BIN)/pip-audit -r requirements/production.txt --require-hashes || $(BIN)/pip-audit -r requirements/production.txt

validate-fe: ## Validate the frontend's GraphQL documents against the schema (skips without a checkout)
	$(PY) scripts/validate_fe_docs.py

ci: lint typecheck check test ## Everything CI runs, in order

# ── Project scaffolding ─────────────────────────────────────────────────────
.PHONY: app secret-key encryption-key oidc-key

## Scaffolding & keys
app: ## Create a business app: make app name=invoices
	@test -n "$(name)" || (echo "usage: make app name=<snake_case_name>"; exit 1)
	$(PY) scripts/new_app.py $(name)

secret-key: ## Print a fresh SECRET_KEY
	@$(PY) -c "from django.core.management.utils import get_random_secret_key as k; print(k())"

encryption-key: ## Print a fresh Fernet key for ENCRYPTION_KEYS
	@$(PY) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"


# ── Deployment (bare metal) ─────────────────────────────────────────────────
.PHONY: deploy-check

## Deployment
deploy-check: ## Prove the production settings load and pass Django's deployment checks (needs a production .env)
	DJANGO_SETTINGS_MODULE=config.settings.production $(PY) -c "import django; django.setup(); print('production settings OK')"
	DJANGO_SETTINGS_MODULE=config.settings.production $(MANAGE) check --deploy --fail-level ERROR

# ── project ── AlgoDial ─────────────────────────────────────────────────────
.PHONY: brain brain-check writer live dev sim seed-demo calltests agentcheck agent-login pjsip-schema

## AlgoDial
brain: ## Run the brain (call control): manage.py run_brain
	$(MANAGE) run_brain

brain-check: ## Check the brain can reach ARI and Redis, then exit
	$(MANAGE) run_brain --check

writer: ## Run the writer: brain events into Event, Call and Leg
	$(MANAGE) run_writer --consumer writer-1

live: ## Run the live relay: brain events to the agent pages' WebSockets
	$(MANAGE) run_live

dev: ## Web (with WebSockets), brain, writer, live relay, recordings keeper and AI worker together; Ctrl-C stops all
	@trap 'kill 0' INT TERM EXIT; \
	$(MANAGE) run_brain & $(MANAGE) run_writer --consumer writer-1 & $(MANAGE) run_live & $(MANAGE) run_recordings & \
	$(MANAGE) run_ai & \
	$(MANAGE) runserver 0.0.0.0:$(PORT)

ai-bakeoff: ## Rank OpenAI's speech, language and voice models for the AI agent (writes docs/AI_BAKEOFF.md)
	$(MANAGE) ai_bakeoff

seed-demo: ## The demo centre of docs/ROUTING_DESIGN.md (agents, skills, rule sets, queues, 801-803); safe to rerun
	$(MANAGE) seed_demo

sim: ## A virtual busy hour through the real brain core, every queue strategy compared (make sim a="--skills")
	$(MANAGE) simulate $(a)

agentcheck: ## M3, M5-M8 in real browsers: agent call, recording, supervisor, AI handoff, every transfer (stop your brain first)
	$(PY) lab/agentpage.py

agent-login: ## Lab agents 7001, 8003, 9003 in queue Support (800), phones 1001/1002 as SIP accounts (passwords into .env)
	$(PY) lab/agent_login.py

calltests: ## Call tests 1-20 on the real lab with scripted phones (stop your brain first; make calltests t="1 4")
	$(PY) lab/calltests.py $(t)

pjsip-schema: ## Asterisk's PJSIP realtime tables (its own Alembic) + Asterisk's read-only role + rewrite accounts
	$(MANAGE) pjsip_schema
