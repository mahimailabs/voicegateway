"""Framework-neutrality guarantees for the VoiceGateway core.

`import voicegateway` must import neither ``livekit`` nor ``pipecat``, even
though at least one of them is installed in the dev environment. The
``LLM``/``STT``/``TTS`` factories stay importable (they pull the framework only
on first attribute access), and ``detect_framework`` classifies objects by their
module string without importing either framework.
"""

from __future__ import annotations

import subprocess
import sys

from voicegateway._frameworks import detect_framework, require_extra


def test_core_import_pulls_no_framework() -> None:
    """A bare `import voicegateway` must not import livekit or pipecat.

    Run in a fresh subprocess so the assertion sees a clean ``sys.modules`` (the
    test process itself may already have imported livekit via other tests).
    """
    code = (
        "import voicegateway, sys; "
        "assert 'livekit' not in sys.modules, 'livekit eagerly imported'; "
        "assert 'pipecat' not in sys.modules, 'pipecat eagerly imported'; "
        "print('PURE')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import voicegateway pulled a framework.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "PURE" in result.stdout


class _FakeType:
    """A stand-in whose class __module__ can be forced for detection tests."""


def _obj_with_module(module: str) -> object:
    """Return an instance whose ``type(...).__module__`` is ``module``."""
    cls = type("Fake", (), {})
    cls.__module__ = module
    return cls()


def test_detect_framework_livekit() -> None:
    assert detect_framework(_obj_with_module("livekit.agents.llm")) == "livekit"
    assert (
        detect_framework(_obj_with_module("livekit.agents.voice.agent_session"))
        == "livekit"
    )


def test_detect_framework_pipecat() -> None:
    assert detect_framework(_obj_with_module("pipecat.services.openai")) == "pipecat"
    assert detect_framework(_obj_with_module("pipecat.pipeline.task")) == "pipecat"


def test_detect_framework_unknown() -> None:
    assert detect_framework(_obj_with_module("builtins")) == "unknown"
    assert detect_framework(object()) == "unknown"
    assert detect_framework(_obj_with_module("livekitten.fake")) == "unknown"


def test_detect_framework_accepts_class_directly() -> None:
    """Passing a class (not an instance) reads the class's own __module__."""
    cls = type("Svc", (), {})
    cls.__module__ = "pipecat.services.cartesia.tts"
    assert detect_framework(cls) == "pipecat"


def test_require_extra_present_is_noop() -> None:
    """livekit is installed in the dev env, so require_extra('livekit') passes."""
    require_extra("livekit")


def test_require_extra_missing_raises_with_hint() -> None:
    """A missing extra raises ImportError carrying the pip install hint."""
    import importlib.util

    if importlib.util.find_spec("definitely_not_a_framework") is not None:
        return  # pragma: no cover - guard against an impossible name colliding
    try:
        require_extra("definitely_not_a_framework")
    except ImportError as exc:
        assert "pip install voicegateway[definitely_not_a_framework]" in str(exc)
    else:  # pragma: no cover - require_extra must raise for a missing extra
        raise AssertionError("require_extra did not raise for a missing extra")


def test_lazy_llm_still_importable() -> None:
    """Accessing voicegateway.LLM triggers the lazy import and returns a factory.

    livekit is installed in the dev env, so the lazy import succeeds and yields a
    callable. This proves the deferral did not break the public factory.
    """
    import voicegateway

    assert callable(voicegateway.LLM)
    assert callable(voicegateway.STT)
    assert callable(voicegateway.TTS)
