"""Tests for ``doctor``'s legacy provider-key validation helper.

``_validate_provider_key`` moved from onboard to doctor when onboard went
framework-agnostic (it no longer configures a provider). doctor's "Provider key
valid" check still uses it when the operator has set a ``providers:`` block
explicitly.
"""

from __future__ import annotations

from voicegateway.utils.cli.doctor import _validate_provider_key


async def test_validate_returns_ok_when_health_check_true(monkeypatch):
    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    class _FakeProvider:
        def __init__(self, *_a, **_kw):
            pass

        async def health_check(self):
            return True

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider", lambda name, cfg: _FakeProvider()
    )
    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "ok"
    assert message is None


async def test_validate_returns_timeout_when_health_check_hangs(monkeypatch):
    import asyncio as _aio

    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    class _FakeProvider:
        async def health_check(self):
            await _aio.Event().wait()  # never returns
            return True  # unreachable

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider", lambda name, cfg: _FakeProvider()
    )
    # Lower the cap so the test stays fast.
    monkeypatch.setattr("voicegateway.utils.cli.doctor.VALIDATION_TIMEOUT_S", 0.05)

    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "timeout"
    assert message is None


async def test_validate_returns_failed_when_health_check_returns_false(monkeypatch):
    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    class _FakeProvider:
        async def health_check(self):
            return False

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider", lambda name, cfg: _FakeProvider()
    )
    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "failed"
    assert "declined" in (message or "")


async def test_validate_returns_failed_when_health_check_raises(monkeypatch):
    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    class _FakeProvider:
        async def health_check(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider", lambda name, cfg: _FakeProvider()
    )
    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "failed"
    assert "connection refused" in (message or "")


async def test_validate_skipped_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )
    status, message = await _validate_provider_key("notathing", "sk-test")
    assert status == "skipped"
    assert "notathing" in (message or "")


async def test_validate_skipped_when_plugin_not_installed(monkeypatch):
    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    def _raise_import_error(name, cfg):
        raise ImportError("livekit-plugins-openai not installed")

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider", _raise_import_error
    )
    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "skipped"
    assert "plugin not installed" in (message or "")
