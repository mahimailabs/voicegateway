# Code Style

VoiceGateway enforces consistent code style through automated tooling. This page documents the rules and conventions.

## Tooling overview

| Tool | Purpose | Config location |
|---|---|---|
| **ruff** | Linting + formatting (replaces flake8, isort, Black) | `pyproject.toml` `[tool.ruff]` |
| **mypy** | Static type checking | `pyproject.toml` `[tool.mypy]` |
| **pre-commit** | Runs checks before each commit | `.pre-commit-config.yaml` |

## Ruff

Ruff handles both linting and formatting. It is configured in `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 88

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort (import sorting)
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade (modernize syntax)
]
ignore = [
    "E501",  # line too long (formatter handles wrapping)
    "B008",  # function calls in argument defaults
    "C901",  # too complex
    "W191",  # indentation contains tabs
]
```

### Running ruff

```bash
# Check for lint errors
ruff check .

# Auto-fix what can be fixed
ruff check --fix .

# Format code (Black-compatible)
ruff format .

# Check formatting without modifying files
ruff format --check .
```

### Import sorting

Ruff's `I` rule handles import sorting (replacing isort). Imports are grouped in this order:

1. Standard library (`import os`, `from typing import ...`)
2. Third-party (`import pytest`, `from fastapi import ...`)
3. Local (`from voicegateway.core import ...`)

Each group is separated by a blank line. Within a group, `import` statements come before `from ... import`.

## mypy

Static type checking catches bugs before runtime. VoiceGateway's mypy config:

```toml
[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
ignore_missing_imports = true
```

Run mypy:

```bash
mypy
```

### Type annotation guidelines

- All public functions must have type annotations
- Use `from __future__ import annotations` at the top of every module (enables PEP 604 `X | Y` syntax)
- Use `dict`, `list`, `tuple` (lowercase) instead of `Dict`, `List`, `Tuple` from `typing`
- Use `X | None` instead of `Optional[X]`
- Use `TYPE_CHECKING` guards for import-only types to avoid circular imports:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicegateway.providers.base import BaseProvider
```

## Docstrings

Use **Google-style docstrings** for all public classes, methods, and functions:

```python
def create_provider(provider_name: str, config: dict[str, Any]) -> BaseProvider:
    """Create a provider instance by name.

    Args:
        provider_name: Name of the provider (e.g., "openai", "deepgram").
        config: Provider configuration dict from voicegateway.yaml.

    Returns:
        Initialized provider instance.

    Raises:
        ValueError: If provider name is unknown.
    """
```

Rules:
- First line is a concise imperative summary (no period for one-liners)
- Blank line between summary and `Args`/`Returns`/`Raises` sections
- `Args`, `Returns`, `Raises` sections as needed
- Private methods (`_foo`) may use shorter docstrings

## Conventional Commits {#conventional-commits}

All commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

| Type | When to use |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or modifying tests |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Performance improvement |
| `chore` | Build process, dependency updates, tooling |
| `ci` | CI/CD changes |

### Scopes

Use the module or area affected:

- `core`, `providers`, `middleware`, `storage`, `pricing`
- `dashboard`, `mcp`, `cli`, `server`
- `config`, `docker`
- Provider names: `openai`, `deepgram`, etc.

### Examples

```
feat(providers): add ElevenLabs TTS support
fix(middleware): prevent double cost tracking on fallback
docs(mcp): add authentication examples
test(storage): cover edge case in daily aggregation query
refactor(core): extract model resolution from gateway to router
chore(deps): bump livekit-agents to 1.6.0
```

### Multi-scope commits

If a change spans multiple scopes, list the primary scope and mention others in the body:

```
feat(mcp): implement project tools (list/get/create/delete)

Also updates storage layer to support project deletion
and adds conftest fixtures for MCP testing.
```

## File organization

- One class per file for providers (`openai_provider.py`, not `providers.py`).
- Group related functions in a module (`middleware/cost_tracker.py`).
- Keep `__init__.py` files minimal -- a docstring, re-exports of the
  subpackage's public API, and an `__all__` declaration. Nothing else.
- Use `from __future__ import annotations` in every module.

### Internal modules

Files whose names start with a leading underscore are internal
implementation details and not part of the public import surface:

- `src/voicegateway/_version.py` -- hatch-vcs generated, do not edit.
- `src/voicegateway/tests/fixtures/streaming/_loader.py` -- private test helper.
- `src/voicegateway/inference/_llm.py`, `_stt.py`, `_tts.py` -- private
  factories; the public surface is `voicegateway.inference.{LLM,STT,TTS}`.

A future ruff rule could enforce that nothing under
`src/voicegateway/` imports a leading-underscore module from a different
subpackage; for now the convention is documentation-only.

## Public API contract

Every package and subpackage `__init__.py` declares an explicit
`__all__` list. This is the v0.1.x public surface:

```python
# voicegateway/server/__init__.py
from voicegateway.server.main import build_app

__all__ = ["build_app"]
```

When `__all__` is the empty list (`__all__: list[str] = []`), the
subpackage exposes nothing at its top level and callers reach into
submodules directly:

```python
# Use this:
from voicegateway.core.gateway import Gateway

# Not this (would fail because voicegateway.core has __all__ = []):
from voicegateway.core import Gateway
```

Names not in `__all__`, and any leading-underscore module, are
internal. They may be renamed or removed in any minor release without
a deprecation cycle.

## Module-level patterns

The codebase converged on a small set of patterns. New code should
follow them unless there is a concrete reason not to.

### `typing.Protocol` vs ABC

Prefer ``typing.Protocol`` for structural typing where multiple
implementations need to satisfy an interface without sharing helper
code (see ``src/voicegateway/cli/tui/data`` for a real example -- the
``DataClient`` Protocol is satisfied by both ``HttpClient`` and
``LocalClient`` without inheritance). Use an abstract base class only
when the base genuinely supplies shared behaviour
(``src/voicegateway/providers/base.py``'s ``BaseProvider`` is the
canonical example: every concrete provider inherits real helper
methods).

### Pydantic for config

Anything parsed from YAML or environment variables is a Pydantic
model. See ``src/voicegateway/core/config.py`` and
``src/voicegateway/core/schema.py`` for the project-wide config shape;
the validators there are the single source of truth for what
``voicegw.yaml`` accepts.

### Async throughout

Every I/O path uses ``async`` / ``await``. Storage reads, provider
calls, HTTP handlers, MCP tools, the dashboard backend -- all async.
Synchronous helpers exist only for pure data transformation (parsing,
formatting). When in doubt, make it async; mixing sync and async
boundaries is the most common source of subtle bugs in this codebase.

### Exception handling

Catch specific exception types where possible. ``except Exception`` is
acceptable at top-level boundaries (provider call sites, MCP tool
dispatch, middleware fallback) where the catch is paired with structured
logging and a controlled fallback. Avoid broad excepts in narrow code
paths -- they hide real bugs and bypass the type system.

## Test patterns

See [Testing](/contributing/testing) for the full guide. Quick
reference:

- Tests live under ``src/voicegateway/tests/`` mirroring the package layout
  (``src/voicegateway/tests/middleware/`` for ``src/voicegateway/middleware/`` tests, etc.).
- ``pytest`` + ``pytest-asyncio`` with ``asyncio_mode = "auto"``
  (configured in ``pyproject.toml``). No ``@pytest.mark.asyncio`` is
  needed; async tests are detected automatically.
- Shared fixtures live in ``src/voicegateway/tests/conftest.py``. Per-subpackage
  fixtures live in that subpackage's ``conftest.py``.
- File-name pattern: ``test_<thing-under-test>.py``. Function-name
  pattern: ``test_<behaviour>``.
- Subprocess CLI tests use the patterns in
  ``src/voicegateway/tests/cli/test_record_streaming_fixtures_cli.py``.
- Coverage stays at or above the project gate (see
  ``[tool.coverage.run]`` in ``pyproject.toml``).

## Naming conventions

| Item | Convention | Example |
|---|---|---|
| Modules | `snake_case` | `cost_tracker.py` |
| Classes | `PascalCase` | `CostTracker` |
| Functions | `snake_case` | `create_provider` |
| Constants | `UPPER_SNAKE_CASE` | `DEFAULT_DB_PATH` |
| Private | `_leading_underscore` | `_PROVIDER_REGISTRY` |
| Type vars | `PascalCase` or single letter | `T` |

## Related pages

- [Development Setup](/contributing/development-setup)
- [Testing](/contributing/testing)
- [Contributing](/contributing/)
