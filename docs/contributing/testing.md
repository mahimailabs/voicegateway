# Testing

VoiceGateway has 200+ tests with over 70% code coverage. This guide covers running tests, writing new ones, and using the shared fixtures.

## Running tests

```bash
# Run all tests
pytest

# Run a specific file
pytest tests/test_config.py

# Run a specific test by name
pytest tests/test_config.py::test_load_example_config

# Run with coverage
pytest --cov

# Run with coverage and show missing lines
pytest --cov --cov-report=term-missing

# Run verbose (see each test name)
pytest -v

# Stop at first failure
pytest -x
```

## pytest configuration

VoiceGateway uses `asyncio_mode = "auto"` in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

This means:
- **No `@pytest.mark.asyncio` needed** -- async test functions are detected automatically
- **All tests run in the same event loop policy** -- no loop conflicts
- Test files live in the `tests/` directory

## Shared fixtures

The `tests/conftest.py` file provides four key fixtures:

### `_test_env` (autouse)

Runs automatically for every test. Sets fake API keys so provider constructors do not fail:

```python
@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    for key in [
        "OPENAI_API_KEY",
        "DEEPGRAM_API_KEY",
        "CARTESIA_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "ELEVENLABS_API_KEY",
        "ASSEMBLYAI_API_KEY",
    ]:
        monkeypatch.setenv(key, "test-key-value")
```

You never need to call this fixture explicitly -- it is autouse.

### `example_config_path`

Returns the path to `voicegw.example.yaml` at the repository root. Use this to test config loading against the shipped example:

```python
def test_load_example(example_config_path):
    config = GatewayConfig.load(example_config_path)
    assert config.providers
```

### `temp_config`

Writes a minimal `voicegw.yaml` to a temporary directory and returns its path. The config includes OpenAI and Deepgram providers, one STT model, one LLM model, two projects (`test-project` and `blocked-project`), and cost tracking enabled:

```python
def test_gateway_init(temp_config):
    gw = Gateway(config_path=temp_config)
    assert gw is not None
```

### `seeded_storage`

Creates a `SQLiteStorage` instance pre-loaded with three sample `RequestRecord` entries:

| Record | Modality | Model | Project | Cost |
|---|---|---|---|---|
| 1 | stt | deepgram/nova-3 | test-project | $0.0043 |
| 2 | llm | openai/gpt-4o-mini | test-project | $0.015 |
| 3 | llm | openai/gpt-4o-mini | default | $0.008 |

```python
async def test_query_costs(seeded_storage):
    costs = await seeded_storage.get_costs(project="test-project")
    assert len(costs) == 2
```

## Writing tests

### Test file naming

- Test files: `tests/test_<module>.py`
- Test functions: `test_<what_it_tests>`
- Test classes (grouping related tests): `TestClassName`

### Async tests

Write async tests as regular `async def` functions. The `asyncio_mode = "auto"` setting handles the rest:

```python
async def test_health_check():
    provider = OpenAIProvider({"api_key": "test-key"})
    # Mock the HTTP call
    result = await provider.health_check()
    assert result is True
```

### Mocking providers

Providers make HTTP calls to external APIs. Always mock these in tests:

```python
from unittest.mock import AsyncMock, patch


async def test_stt_fallback():
    with patch(
        "voicegateway.providers.deepgram_provider.DeepgramProvider.health_check",
        new_callable=AsyncMock,
        return_value=False,
    ):
        # Deepgram is "down", fallback should kick in
        ...
```

### Mocking the config

Use `temp_config` for tests that need a Gateway instance, or construct configs directly:

```python
def test_router_resolution(temp_config):
    config = GatewayConfig.load(temp_config)
    router = Router(config)
    provider, model = router.resolve("openai/gpt-4o-mini", "llm")
    assert model == "gpt-4o-mini"
```

### Testing cost calculations

```python
from voicegateway.pricing.catalog import get_pricing


def test_deepgram_nova3_pricing():
    pricing = get_pricing("deepgram/nova-3", "stt")
    assert pricing["per_minute"] == 0.0043
```

### Testing middleware

Middleware wraps provider calls. Test the wrapping behavior:

```python
async def test_budget_enforcer_blocks():
    """Budget enforcer should raise when project exceeds daily budget."""
    enforcer = BudgetEnforcer(storage=seeded_storage, config=config)
    with pytest.raises(BudgetExceededError):
        await enforcer.check("blocked-project")
```

## Mock patterns

### `monkeypatch.setenv` for environment variables

```python
def test_custom_db_path(monkeypatch, tmp_path):
    db_path = str(tmp_path / "custom.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    gw = Gateway(config_path=temp_config)
    assert gw._storage is not None
```

### `tmp_path` for temporary files

pytest's built-in `tmp_path` fixture provides a temporary directory unique to each test:

```python
async def test_sqlite_storage(tmp_path):
    storage = SQLiteStorage(str(tmp_path / "test.db"))
    await storage.log_request(record)
```

### `patch` for external HTTP calls

```python
from unittest.mock import patch, MagicMock

def test_provider_creation():
    with patch("voicegateway.providers.openai_provider.openai") as mock_openai:
        provider = OpenAIProvider({"api_key": "test"})
        llm = provider.create_llm("gpt-4o-mini")
        assert llm is not None
```

## Coverage expectations

- New features must include tests
- Bug fixes should include a regression test
- Target: maintain above 70% overall coverage
- Critical paths (Gateway, Router, CostTracker, BudgetEnforcer) should be above 90%

Check coverage for specific modules:

```bash
pytest --cov=voicegateway.core --cov-report=term-missing
pytest --cov=voicegateway.middleware --cov-report=term-missing
```

## Related pages

- [Development Setup](/contributing/development-setup)
- [Code Style](/contributing/code-style)
- [Adding a Provider](/contributing/adding-a-provider)
- [Contributing](/contributing/)
