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

    A subclass of a framework base class (defined in user/library code outside
    the framework package, e.g. a custom ``livekit.agents.llm.LLM`` subclass) is
    still detected: the whole MRO's ``__module__`` strings are inspected, so the
    inherited framework base is found. Only already-loaded classes are read; no
    framework is imported to do this.
    """
    # A class carries its own __module__; an instance's class carries it. Prefer
    # the object's own __module__ when obj is a class so callers can pass either.
    module = getattr(obj, "__module__", None)
    if not isinstance(module, str) or not isinstance(obj, type):
        module = getattr(type(obj), "__module__", "")

    root = module.split(".", 1)[0] if isinstance(module, str) else ""
    if root == "livekit":
        return "livekit"
    if root == "pipecat":
        return "pipecat"

    # Fall back to the MRO: a subclass defined outside the framework package
    # still inherits from a framework base whose __module__ names the framework.
    cls = obj if isinstance(obj, type) else type(obj)
    for base in getattr(cls, "__mro__", ()):
        base_module = getattr(base, "__module__", "")
        if not isinstance(base_module, str):
            continue
        base_root = base_module.split(".", 1)[0]
        if base_root == "livekit":
            return "livekit"
        if base_root == "pipecat":
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
