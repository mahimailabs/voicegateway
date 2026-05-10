# Convenience aliases for the canonical commands. Each target shells
# out to the same uv-based invocation that .github/workflows/ci.yml
# and test-coverage.yml run, so local `make` matches the CI gate
# exactly. design.md §6 AC2/AC3 reference these target names directly.

.PHONY: help test coverage lint typecheck check

help:
	@echo "Targets:"
	@echo "  test       Run pytest (matches ci.yml)"
	@echo "  coverage   Run pytest with coverage and the 80% gate (matches test-coverage.yml)"
	@echo "  lint       Run ruff (matches ci.yml)"
	@echo "  typecheck  Run mypy (matches ci.yml)"
	@echo "  check      Run lint, typecheck, and test in sequence"

test:
	uv run pytest -q --ignore=tests/test_providers/test_ollama.py

coverage:
	uv run coverage run -m pytest tests/ --ignore=tests/test_providers/test_ollama.py
	uv run coverage report

lint:
	uv run ruff check voicegateway dashboard tests

typecheck:
	uv run mypy voicegateway dashboard

check: lint typecheck test
