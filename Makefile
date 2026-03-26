.PHONY: help format lint test serve dev dev-backend dev-frontend clean

# Load environment variables from .env if it exists
ifneq (,$(wildcard .env))
include .env
endif

# Default ports if not set in .env
FRONTEND_PORT ?= 3000
BACKEND_PORT ?= 8000

## help: Display this help message
help:
	@awk 'BEGIN {print "Available targets:"} /^## / {gsub(/^## /, ""); split($$0, a, ": "); printf "  %-18s %s\n", a[1], a[2]}' $(MAKEFILE_LIST)

## format: Format Python code with ruff
format:
	uv run ruff format .

## lint: Lint Python code with ruff
lint:
	uv run ruff check . --fix

## test: Run all tests with pytest
test:
	uv run pytest

## serve: Start the FastAPI backend server
serve:
	BACKEND_PORT=$(BACKEND_PORT) uv run python main.py

## rebuild: Rebuild the problem dataset (run when generate.py changes)
rebuild:
	@if [ -d ./problems ]; then \
		echo "Error: problems/ directory already exists. Remove it before rebuilding."; \
		exit 1; \
	fi
	uv run python scripts/generate.py

## verify: Check the integrity of the problem dataset
verify:
	uv run python scripts/verify.py

## dev-backend: Start the FastAPI backend server (alias for serve)
dev-backend:
	BACKEND_PORT=$(BACKEND_PORT) uv run python main.py

## dev-frontend: Start the Vite frontend dev server
dev-frontend:
	cd frontend && FRONTEND_PORT=$(FRONTEND_PORT) BACKEND_PORT=$(BACKEND_PORT) pnpm dev

## dev: Start both backend and frontend servers together
dev:
	@command -v tmux >/dev/null 2>&1 && ./scripts/tmux-dev.sh || (echo "tmux not found, using dev.sh instead..." && ./scripts/dev.sh)

## clean: Remove build artifacts, cache, and dependencies
clean:
	@echo "Cleaning up..."
	@rm -rf __pycache__ .pytest_cache .venv build dist
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete
	@find . -type f -name '*.pyo' -delete
	@rm -rf frontend/node_modules frontend/dist frontend/.vite
	@echo "Clean complete!"

## cloc: Count lines of code
cloc:
	cloc server scripts static tests
