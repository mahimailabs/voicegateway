# Development Setup

This guide walks you through setting up a local development environment for VoiceGateway.

## Prerequisites

- **Python 3.11+** -- check with `python --version`
- **Node.js 18+** -- for the dashboard frontend (`node --version`)
- **Git** -- for version control
- **Docker** (optional) -- for running containerized tests or local Ollama

## Clone and install

```bash
# Fork the repo on GitHub first, then:
git clone https://github.com/<your-username>/voicegateway.git
cd voicegateway

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install with all development extras
pip install -e ".[all,dashboard,mcp,dev]"
```

This installs:
- All 11 provider SDKs (`all`)
- Dashboard dependencies (`dashboard`)
- MCP server dependencies (`mcp`)
- Test tools: pytest, pytest-asyncio, pytest-cov (`dev`)

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
pytest tests/test_config.py

# Run a specific test
pytest tests/test_config.py::test_name

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
mypy voicegateway

# Tests
pytest

# CLI
voicegw --version
voicegw status
```

## Dashboard development {#dashboard}

The dashboard has a FastAPI backend and a React/TypeScript/Vite frontend.

### Backend

The dashboard API lives in `dashboard/api/`. It starts automatically when you run:

```bash
voicegw dashboard
```

This serves the API on port 9090 and the frontend (if built) from `dashboard/frontend/dist/`.

### Frontend

```bash
cd dashboard/frontend

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

## Documentation site {#documentation-site}

The docs site uses VitePress and lives in `docs/`.

```bash
cd docs

# Install dependencies
npm install

# Start dev server (hot reload)
npm run dev

# Build for production
npm run build
```

The dev server runs on `http://localhost:5173` (or the next available port).

### Deploying docs to Cloudflare Pages

The docs are deployed to Cloudflare Pages. To set up deployment:

1. **Connect your repository** in the [Cloudflare Dashboard](https://dash.cloudflare.com/) under Workers & Pages
2. **Configure the build:**
   - Build command: `cd docs && npm install && npm run build`
   - Build output directory: `docs/.vitepress/dist`
   - Root directory: `/` (repository root)
   - Environment variable: `NODE_VERSION` = `18`
3. **Set up custom domain** (optional) in the Cloudflare Pages project settings
4. **Preview deployments** are created automatically for every PR

Alternatively, deploy manually:

```bash
cd docs
npm run build
npx wrangler pages deploy .vitepress/dist --project-name=voicegateway-docs
```

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
  docs/                  # VitePress documentation
  pyproject.toml         # Project metadata, dependencies, tool config
  voicegw.example.yaml   # Example configuration
```

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

- [Contributing](/contributing/)
- [Code Style](/contributing/code-style)
- [Testing](/contributing/testing)
- [Adding a Provider](/contributing/adding-a-provider)
