"""VoiceGateway: framework-neutral cost tracking for voice agents.

The core is framework-neutral: ``import voicegateway`` imports neither
``livekit`` nor ``pipecat``. Both frameworks are optional extras
(``pip install voicegateway[livekit]`` / ``voicegateway[pipecat]``).

``attach`` and ``register_worker`` are safe to import eagerly (their modules do
not import any framework at load time; ``attach`` duck-types its target and
lazily imports the framework it detects). ``Observer`` pulls pipecat on first
use and is therefore imported lazily via a module-level ``__getattr__``
(PEP 562): accessing ``voicegateway.Observer`` triggers the import, but a bare
``import voicegateway`` does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway._version import __version__
from voicegateway.fleet.worker import register_worker
from voicegateway.guard import guard
from voicegateway.inference.session.attach import attach

if TYPE_CHECKING:
    # Type-checkers resolve these eagerly (they do not run the module), so the
    # public names stay visible to tooling without importing pipecat at runtime.
    from voicegateway import inference
    from voicegateway.inference.pipecat.observer import (
        VoiceGatewayObserver as Observer,
    )

__all__ = [
    "Observer",
    "attach",
    "guard",
    "inference",
    "register_worker",
    "__version__",
]

# Names exposed lazily via PEP 562 __getattr__. ``Observer`` pulls pipecat on
# first access, so it must not be imported at module load (that would break core
# framework neutrality).
_LAZY = {
    "Observer": ("voicegateway.inference.pipecat.observer", "VoiceGatewayObserver"),
    "inference": ("voicegateway.inference", None),
}

# The extra each lazy name needs, so a missing framework yields the friendly
# "pip install voicegateway[<extra>]" error instead of a raw ModuleNotFoundError.
# ``inference`` is intentionally absent: the submodule itself is framework-neutral,
# so pipecat-only users can reach ``voicegateway.inference.pipecat`` without livekit.
_LAZY_EXTRA = {
    "Observer": "pipecat",
}


def __getattr__(name: str) -> Any:
    """Lazily resolve framework-pulling public names (PEP 562).

    Keeps ``import voicegateway`` free of any framework import while preserving
    ``voicegateway.Observer`` (pipecat) and the ``voicegateway.inference``
    submodule as attribute-access entry points. When the backing extra is not
    installed, raises the friendly ``require_extra`` error (with a pip hint)
    rather than a raw ModuleNotFoundError.
    """
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    extra = _LAZY_EXTRA.get(name)
    if extra is not None:
        from voicegateway._frameworks import require_extra

        require_extra(extra)  # no-op when installed; friendly ImportError otherwise
    import importlib

    module_name, attr = target
    module = importlib.import_module(module_name)
    value = module if attr is None else getattr(module, attr)
    # Cache on the module so subsequent lookups skip __getattr__.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
