# Development Setup

## Prerequisites

- **Python 3.11+** (`python --version`)
- **Git**
- **Node.js 18+** if you are working on the dashboard frontend (`src/dashboard/frontend/`)

## Clone and install
### Fork and clone
```bash
git clone https://github.com/<your-username>/voicegateway.git
cd voicegateway
```
### Create a virtual environment and install
```bash uv (preferred)
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

uv pip install -e ".[dev]"
```

```bash pip (alternate)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```
The `dev` extra pulls in the `livekit` and `dashboard` extras plus pytest, ruff-adjacent tooling not covered by pre-commit (mypy is installed separately, see below), and the ClickHouse/DuckDB test dependencies. Every `voicegw` subcommand needs the `livekit` extra: `cli/__init__.py` imports the LiveKit CLI module at package load time, so an editable install without it fails to import `voicegateway.cli` at all.
## Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
```

The hooks run `ruff check --fix` and `ruff format` on every commit (`.pre-commit-config.yaml`). They do not run mypy: mypy runs in CI only. Run it locally before pushing:

```bash
uv run --with 'mypy<2' --with types-PyYAML mypy
```

See [Code Style](code-style.md) for why mypy is pinned below 2.

To run the pre-commit hooks manually on all files:

```bash
pre-commit run --all-files
```

## Running tests

```bash
pytest                                                          # everything
pytest src/voicegateway/tests/core/test_config.py               # one file
pytest src/voicegateway/tests/core/test_config.py::test_name    # one test
pytest --cov                                                     # with coverage
pytest -v                                                         # verbose
```

`asyncio_mode = "auto"` is set in `pyproject.toml`, so async test functions run without a `@pytest.mark.asyncio` decorator. See [Testing](testing.md) for fixtures and patterns.

## Verify everything works

```bash
ruff check .
uv run --with 'mypy<2' --with types-PyYAML mypy
pytest
voicegw --version
voicegw status
```

## Dashboard frontend

There is no separate dashboard backend process. `voicegw serve` (the daemon) serves both `/v1/*` and `/api/*` plus the built React SPA at `/`, all on one port (`0.0.0.0:8080` by default). `voicegw dashboard` does not start anything; it opens your browser at that same address.

To work on the frontend with hot reload:

```bash
voicegw serve            # in one terminal
cd src/dashboard/frontend
npm install
npm run dev              # in another terminal
```

The Vite dev server runs on `http://localhost:5173` and proxies `/api` and `/v1` to `http://localhost:8080`. `npm run build` produces the production bundle the daemon serves from disk.

## Documentation site

Docs source lives in this repo under `site/docs/` (a Fumadocs app, pages in `site/docs/content/docs/`), published at `https://docs.voicegateway.dev` from the default branch. Change the docs in the same PR as any behavior or API change; see [Contributing](index.md).

## Environment variables for development

The `_test_env` autouse fixture sets fake API keys for the whole test suite, so you do not need real provider keys to run `pytest`. For manual testing against real providers:

```bash
export OPENAI_API_KEY=sk-...
export DEEPGRAM_API_KEY=...
export ANTHROPIC_API_KEY=...
export CARTESIA_API_KEY=...
```

## Related pages

- [Code Style](code-style.md)
- [Testing](testing.md)
- [Adding a Provider](adding-a-provider.md)
- [Refreshing Pricing](refreshing-pricing.md)
