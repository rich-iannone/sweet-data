.PHONY: help install install-dev test test-cov lint format type-check clean build dev run

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install the package
	uv pip install -e .

install-dev: ## Install the package with development dependencies
	uv pip install -e ".[dev]"

test: ## Run tests
	pytest

test-cov: ## Run tests with coverage
	pytest --cov=sweet --cov-report=term-missing --cov-report=html

lint: ## Run linting
	ruff check sweet tests

lint-fix: ## Run linting with auto-fix
	ruff check --fix sweet tests

format: ## Format code
	ruff format sweet tests

format-check: ## Check code formatting
	ruff format --check sweet tests

quality: lint format-check ## Run all quality checks

clean: ## Clean up build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete

build: clean ## Build the package
	uv build

dev: install-dev ## Set up development environment
	pre-commit install

run: ## Run the Sweet application
	python -m sweet

demo: ## Run with demo data
	python -m sweet --demo

# Development workflow targets
watch-test: ## Run tests in watch mode
	pytest-watch

serve-docs: ## Serve documentation locally
	@echo "Documentation serving not yet implemented"

# CI/CD helpers
ci-test: ## Run tests for CI
	pytest --cov=sweet --cov-report=xml

ci-quality: ## Run quality checks for CI
	ruff check sweet tests
	ruff format --check sweet tests
