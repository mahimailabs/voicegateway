"""Framework detection and missing-extra errors (no eager framework imports).

The engine core is framework-neutral: ``import voicegateway`` must not import
``livekit`` or ``pipecat``. ``attach()`` and (later) ``guard()`` need to decide
which framework a target object belongs to and, when the matching extra is not
installed, raise a clear, actionable error. Both helpers here do that purely by
inspecting the object's type/module string, so importing this module (or calling
``detect_framework``) never imports either framework.
"""

from __future__ import annotations

from typing import Any

# Import name each extra makes available, for the "is it installed?" check.
# Extras whose distribution name differs from the import name go here; anything
# not listed falls back to the extra name itself.
_EXTRA_IMPORT = {
    "livekit": "livekit",
    "pipecat": "pipecat",
}


def detect_framework(obj: Any) -> str:
    """Return the framework a target object belongs to, without importing it.

    Inspects ``type(obj).__module__`` (and the object's own ``__module__`` when
    it is itself a class) for a top-level package prefix. Returns one of:

    - ``"livekit"`` when the defining module is under the ``livekit`` package
      (e.g. ``livekit.agents.llm``, ``livekit.agents.voice.agent_session``).
    - ``"pipecat"`` when the defining module is under the ``pipecat`` package
      (e.g. ``pipecat.services.openai.llm``, ``pipecat.pipeline.task``).
    - ``"unknown"`` for anything else.

    No framework is imported: only the already-set ``__module__`` string on the
    object (or its type) is read.
    """
    # A class carries its own __module__; an instance's class carries it. Prefer
    # the object's own __module__ when obj is a class so callers can pass either.
    module = getattr(obj, "__module__", None)
    if not isinstance(module, str) or not isinstance(obj, type):
        module = getattr(type(obj), "__module__", "")
    if not isinstance(module, str):
        return "unknown"

    root = module.split(".", 1)[0]
    if root == "livekit":
        return "livekit"
    if root == "pipecat":
        return "pipecat"
    return "unknown"


def _extra_installed(extra: str) -> bool:
    """Whether the framework backing ``extra`` is importable, without importing it."""
    import importlib.util

    import_name = _EXTRA_IMPORT.get(extra, extra)
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError):
        return False


def require_extra(extra: str) -> None:
    """Raise a clear ImportError with a pip hint if ``extra`` is not installed.

    Example message: ``"voicegateway[pipecat] is required for this operation.
    Install it with: pip install voicegateway[pipecat]"``. A no-op when the
    backing framework is already importable.
    """
    if _extra_installed(extra):
        return
    hint = f"pip install voicegateway[{extra}]"
    raise ImportError(
        f"voicegateway[{extra}] is required for this operation. Install it with: {hint}"
    )


__all__ = ["detect_framework", "require_extra"]
