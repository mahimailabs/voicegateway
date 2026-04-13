"""Shared pytest fixtures."""

import os
import pytest


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """Set fake API keys for all tests."""
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
    yield


@pytest.fixture
def example_config_path():
    """Return path to example config."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "voicegw.example.yaml",
    )
