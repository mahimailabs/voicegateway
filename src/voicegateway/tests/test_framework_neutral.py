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


def test_observer_symbol_is_lazy_and_pure() -> None:
    """Referencing ``voicegateway.Observer`` must not import pipecat.

    ``Observer`` is a lazily-resolved public name (like ``LLM``): it appears in
    ``dir(voicegateway)`` and is importable, but the pipecat import only happens
    when the class is actually resolved/instantiated. This subprocess asserts
    that ``import voicegateway`` plus a ``dir()`` reference stays pipecat-free,
    then that resolving the attribute does pull pipecat (so the lazy import is
    wired, not merely absent).
    """
    # Purity holds whether or not pipecat is installed: importing voicegateway
    # and referencing Observer via dir() must not pull pipecat.
    purity = (
        "import voicegateway, sys; "
        "assert 'Observer' in dir(voicegateway), 'Observer not exported'; "
        "assert 'pipecat' not in sys.modules, 'pipecat imported by import/dir()'; "
        "print('PURE')"
    )
    result = subprocess.run(
        [sys.executable, "-c", purity],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import purity broke.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "PURE" in result.stdout

    # Resolving the attribute is where the framework is pulled. With pipecat
    # installed it lazily imports it; without it (e.g. CI's livekit-only env) it
    # must raise the friendly extra error, not a raw ModuleNotFoundError.
    import importlib.util

    if importlib.util.find_spec("pipecat") is not None:
        resolve = (
            "import voicegateway, sys; "
            "_ = voicegateway.Observer; "
            "assert 'pipecat' in sys.modules, 'Observer did not lazy-import pipecat'; "
            "print('LAZY')"
        )
        expect = "LAZY"
    else:
        resolve = (
            "import voicegateway\n"
            "try:\n"
            "    voicegateway.Observer\n"
            "except ImportError as e:\n"
            "    assert 'voicegateway[pipecat]' in str(e), str(e)\n"
            "    print('FRIENDLY')\n"
            "else:\n"
            "    raise SystemExit('expected ImportError for the missing pipecat extra')\n"
        )
        expect = "FRIENDLY"
    r = subprocess.run([sys.executable, "-c", resolve], capture_output=True, text=True)
    assert r.returncode == 0, f"Observer resolve failed.\nstderr: {r.stderr}"
    assert expect in r.stdout


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


