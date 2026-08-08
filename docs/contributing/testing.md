---
title: "Testing"
description: "pytest setup, shared fixtures, async test patterns, and coverage expectations for VoiceGateway."
---
Tests live under `src/voicegateway/tests/`, mirroring the package layout (`tests/middleware/` for `middleware/`, `tests/core/` for `core/`, and so on). The coverage gate is `fail_under` under `[tool.coverage.report]` in `pyproject.toml`.

## Running tests

```bash
# Run all tests
pytest

# Run a specific file
pytest src/voicegateway/tests/core/test_config.py

# Run a specific test by name
pytest src/voicegateway/tests/core/test_config.py::test_load_example_config

# Run with coverage
pytest --cov

# Run with coverage and show missing lines
pytest --cov --cov-report=term-missing

# Stop at first failure
pytest -x
```

## pytest configuration

`pyproject.toml` sets:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["src/voicegateway/tests"]
```

`asyncio_mode = "auto"` means async `def test_...` functions run without a `@pytest.mark.asyncio` decorator.

## Shared fixtures

`src/voicegateway/tests/conftest.py` defines:

### `_test_env` (autouse)

Sets fake API keys (`OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `ELEVENLABS_API_KEY`, `ASSEMBLYAI_API_KEY`) via `monkeypatch` so provider constructors never fail on a missing key. Runs for every test automatically.

### `_isolate_db_path_env` (autouse)

Snapshots and restores `VOICEGW_DB_PATH` around each test. `VOICEGW_DB_PATH` beats every other DB path source, so a test that sets it and forgets to unset it redirects every later `StorageService` in the run into one shared file. If you see `no such table` on a test's own tmp database, or a UNIQUE-constraint collision between unrelated tests, this is usually the cause: check for a leaked `monkeypatch.setenv("VOICEGW_DB_PATH", ...)` outside this fixture's protection.

### `example_config_path`

Writes the bundled starter config (`src/voicegateway/data/voicegw.example.yaml`) to a tmp file and returns its path:

```python
def test_load_example_config(example_config_path):
    from voicegateway.core.config import GatewayConfig

    config = GatewayConfig.load(example_config_path)
    assert config.providers
```

### `temp_config`

Writes a minimal `voicegw.yaml` (OpenAI + Deepgram providers, one STT model, one LLM model, a `test-project` and `blocked-project`, cost tracking on) to a tmp directory, points `VOICEGW_DB_PATH` at an isolated file, and returns the config path:

```python
def test_gateway_init(temp_config):
    from voicegateway.core.gateway import Gateway

    gw = Gateway(config_path=temp_config)
    assert gw is not None
```

### `seeded_storage`

Async fixture. Creates a `StorageService` (`src/voicegateway/services/storage_service.py`) pre-loaded with three sample `RequestRecord` rows:

| Modality | Model | Project | Cost |
|---|---|---|---|
| stt | deepgram/nova-3 | test-project | $0.0043 |
| llm | openai/gpt-4o-mini | test-project | $0.015 |
| llm | openai/gpt-4o-mini | default | $0.008 |

```python
async def test_query_costs(seeded_storage):
    summary = await seeded_storage.get_cost_summary("today", project="test-project")
    assert summary["total"] == pytest.approx(0.0043 + 0.015)
```

## Writing tests

File-name pattern: `test_<module>.py`. Function-name pattern: `test_<behaviour>`.

### Async tests

Write plain `async def test_...` functions; `asyncio_mode = "auto"` picks them up without a decorator. The next section has a full example.

### Mocking a provider's health check

`BaseProvider.health_check()` is the only method a provider subclass exercises in production (the health-check surface: dashboard **Test Connection**, `voicegw doctor`, the MCP server's admin `test_provider` tool). Follow `src/voicegateway/tests/providers/test_cartesia_health_check.py`: mock `httpx.AsyncClient`, not the provider method itself.

```python
from unittest.mock import AsyncMock, MagicMock, patch

from voicegateway.inference.providers.cartesia_provider import CartesiaProvider


async def test_health_check_returns_false_on_400():
    provider = CartesiaProvider({"api_key": "sk_car_test"})
    response = MagicMock(status_code=400)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("httpx.AsyncClient", return_value=ctx):
        assert await provider.health_check() is False
```

### Resolving a `provider/model` string

```python
from voicegateway.core.model_resolution import ModelResolutionError, resolve_model


def test_resolve_model():
    assert resolve_model("deepgram/nova-3") == ("deepgram", "nova-3")


def test_resolve_model_unknown_provider():
    with pytest.raises(ModelResolutionError, match="Unknown provider"):
        resolve_model("made-up-co/whisper-1")
```

### Testing cost calculations

```python
from decimal import Decimal

from voicegateway.inference.pricing import stt


def test_deepgram_nova3_pricing():
    assert stt.calculate_stt_cost("deepgram/nova-3", 60) == Decimal("0.0048")
```

`voicegateway.inference.pricing.catalog.calculate_cost(modality, model, **units)` is the modality-dispatching entry point; `stt.py` / `llm.py` / `tts.py` do the actual voice-prices lookup per modality. See [Refreshing Pricing](/contributing/refreshing-pricing).

### Testing storage directly

```python
import time
import uuid

from voicegateway.models.request_model import RequestRecord
from voicegateway.services.storage_service import StorageService


async def test_log_request(tmp_path):
    storage = StorageService(str(tmp_path / "test.db"))
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="test",
            cost_usd=0.01,
        )
    )
    rows = await storage.get_recent_requests(limit=10)
    assert rows[0]["model_id"] == "deepgram/nova-3"
```

### `monkeypatch.setenv` for environment variables

```python
def test_custom_db_path(monkeypatch, tmp_path, temp_config):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "custom.db"))
    from voicegateway.core.gateway import Gateway

    gw = Gateway(config_path=temp_config)
    assert gw.storage is not None
```

## Coverage expectations

- New features must include tests.
- Bug fixes should include a regression test.
- The suite must stay at or above `fail_under` in `pyproject.toml`'s `[tool.coverage.report]`.

```bash
pytest --cov=voicegateway.core --cov-report=term-missing
pytest --cov=voicegateway.middleware --cov-report=term-missing
```

## Related pages

- [Development Setup](/contributing/development-setup)
- [Code Style](/contributing/code-style)
- [Adding a Provider](/contributing/adding-a-provider)
- [Contributing](/contributing/index)
