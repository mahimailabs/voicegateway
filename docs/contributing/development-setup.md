---
title: "Development Setup"
description: "Set up a local development environment for VoiceGateway: clone, install, run tests, and work on the dashboard."
---

# Development Setup

This guide walks you through setting up a local development environment for VoiceGateway.

## Prerequisites

- **Python 3.11+** -- check with `python --version`
- **Node.js 18+** -- for the dashboard frontend (`node --version`)
- **Git** -- for version control
- **Docker** (optional) -- for running containerized tests or local Ollama

## Clone and install

<Steps>
  <Step title="Fork and clone">
    Fork the repo on GitHub, then clone your fork:

    ```bash
    git clone https://github.com/<your-username>/voicegateway.git
    cd voicegateway
    ```
  </Step>
  <Step title="Create a virtual environment and install">
    <CodeGroup>
    ```bash uv (preferred)
    uv venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate

    # Install with all development extras
    uv pip install -e ".[all,dashboard,mcp,dev]"
    ```

    ```bash pip (alternate)
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate

    pip install -e ".[all,dashboard,mcp,dev]"
    ```
    </CodeGroup>

    This installs:
    - All 11 provider SDKs (`all`)
    - Dashboard dependencies (`dashboard`)
    - MCP server dependencies (`mcp`)
    - Test tools: pytest, pytest-asyncio, pytest-cov (`dev`)
  </Step>
</Steps>

## Pre-commit hooks

Install pre-commit hooks to catch issues before committing:

```bash
pip install pre-commit
pre-commit install
```

The hooks run:
- **ruff check** -- linting (pycodestyle, pyflakes, isort, bugbear, comprehensions, pyupgrade)
- **ruff format** -- code formatting (Black-compatible)
- **mypy** -- type checking

To run hooks manually on all files:

```bash
pre-commit run --all-files
```

## Running tests

```bash
# Run all tests
pytest

# Run a specific file
pytest src/voicegateway/tests/core/test_config.py

# Run a specific test
pytest src/voicegateway/tests/core/test_config.py::test_name

# Run with coverage report
pytest --cov

# Run with verbose output
pytest -v
```

Tests use `asyncio_mode = "auto"` so you do not need `@pytest.mark.asyncio` decorators. See the [testing guide](/contributing/testing) for details on writing tests and using fixtures.

## Verify everything works

```bash
# Linting
ruff check .

# Type checking
mypy

# Tests
pytest

# CLI
voicegw --version
voicegw status
```

## Dashboard development

The dashboard has a FastAPI backend and a React/TypeScript/Vite frontend.

### Backend

The dashboard API lives in `src/dashboard/api/`. It starts automatically when you run:

```bash
voicegw dashboard
```

This serves the API on port 9090 and the frontend (if built) from `src/dashboard/frontend/dist/`.

### Frontend

```bash
cd src/dashboard/frontend

# Install dependencies
npm install

# Start dev server (hot reload)
npm run dev

# Build for production
npm run build
```

The dev server runs on `http://localhost:5173` and proxies API requests to the dashboard backend on port 9090.

The frontend uses:
- **React 18** with TypeScript
- **Vite** for bundling
- **Recharts** for cost and latency visualizations
- **Neo-Brutalism** design aesthetic (bold borders, solid shadows, high contrast)

## Documentation site

VoiceGateway owns the Markdown source under `docs/`. The rendered docs site is published at `https://docs.voicegateway.dev` via Mintlify.

When docs changes reach `main`, `.github/workflows/docs.yml` triggers a Mintlify rebuild through the configured deploy hook secret.

## Project structure

```
voicegateway/
  voicegateway/
    __init__.py          # Public API: Gateway, ModelId, GatewayConfig
    core/
      gateway.py         # Main orchestrator
      config.py          # YAML config parser
      router.py          # provider/model resolution
      registry.py        # Provider name -> class mapping
      model_id.py        # "provider/model" string parser
    providers/
      base.py            # BaseProvider ABC
      openai_provider.py # One file per provider (11 total)
      ...
    middleware/
      cost_tracker.py    # Per-request cost calculation
      budget_enforcer.py # Daily budget checks
      fallback.py        # Fallback chain logic
      rate_limiter.py    # Per-provider rate limiting
      latency_monitor.py # TTFB + total latency
      logger.py          # Request metadata logging
    storage/
      sqlite.py          # SQLite backend
      models.py          # RequestRecord dataclass
    pricing/
      catalog.py         # Per-model pricing data
    server.py            # FastAPI HTTP API
    cli.py               # Typer CLI
    mcp/                 # MCP server (17 tools)
  dashboard/
    api/                 # Dashboard FastAPI backend
    frontend/            # React/TypeScript/Vite frontend
  tests/
    conftest.py          # Shared fixtures
    test_*.py            # Test files
  docs/                  # Markdown docs source
  pyproject.toml         # Project metadata, dependencies, tool config
```

The starter config template that `voicegw init` writes lives at `src/voicegateway/data/voicegw.example.yaml` (loaded via `importlib.resources` so the wheel ships it).

## Environment variables for development

Tests use fake API keys set by the `_test_env` autouse fixture, so you do not need real keys to run the test suite. For manual testing against real providers, set:

```bash
export OPENAI_API_KEY=sk-...
export DEEPGRAM_API_KEY=...
export ANTHROPIC_API_KEY=...
export CARTESIA_API_KEY=...
# etc.
```

## Related pages

- [Contributing](/contributing/index)
- [Code Style](/contributing/code-style)
- [Testing](/contributing/testing)
- [Adding a Provider](/contributing/adding-a-provider)
