PYTHON ?= $(shell command -v python3.13 2>/dev/null || echo $(HOME)/.local/bin/python3.13)
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
HASS := $(VENV)/bin/hass

DEV_CONFIG := .dev-config
INTEGRATION := custom_components/weather_plus

.PHONY: help install test lint format build run clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV)/.deps: requirements_test.txt
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r requirements_test.txt ruff
	@touch $@

install: $(VENV)/.deps ## Create venv and install dev deps

test: install ## Run pytest
	$(PYTEST) --tb=short

lint: install ## Run ruff (check + format check)
	$(RUFF) check .
	$(RUFF) format --check .

format: install ## Apply ruff autofix and format
	$(RUFF) check --fix .
	$(RUFF) format .

build: lint test ## Run lint and tests

$(DEV_CONFIG)/custom_components:
	@mkdir -p $@

$(DEV_CONFIG)/custom_components/weather_plus: | $(DEV_CONFIG)/custom_components
	@ln -sfn $(CURDIR)/$(INTEGRATION) $@

run: install $(DEV_CONFIG)/custom_components/weather_plus ## Launch a local HA with this integration symlinked
	$(HASS) -c $(DEV_CONFIG)

clean: ## Remove venv, caches, and dev config
	rm -rf $(VENV) $(DEV_CONFIG)
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
