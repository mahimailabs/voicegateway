"""Named capture policies: what to collect, said once instead of four times.

Capture was four booleans on ``attach()``, each with its own default and its own
environment kill-switch. The SET is the interesting thing and there was no way
to name it. An operator who wanted "timing only, nothing the caller said" had to
know that means three of the four off, get each one right, repeat it at every
call site, and know which variable overrides which. That is a policy expressed
as four independent booleans, which is how a wrong combination ships without
anybody deciding to ship it.

The flags also mix two unrelated questions, and the policy names separate them:

* **What does it cost to run?** Dead air polls once a second for the life of
  every session. Turn capture costs nothing between events.
* **What does it disclose?** A transcript is what the caller said. A snapshot is
  the operator's own system prompt and every tool payload.

``lean`` and ``timing_only`` differ only on the first axis; ``timing_only`` and
``standard`` differ only on the second. That is the distinction four booleans
could express and could not NAME.
"""

from __future__ import annotations

from typing import Final

#: What each policy turns on. Keys are the four capture switches; tool-call rows
#: ride ``turns``, because they correlate to a turn and their ``turn_index`` is
#: meaningless without one.
CAPTURE_POLICIES: Final[dict[str, dict[str, bool]]] = {
    # Today's defaults, so `policy="standard"` and passing nothing agree.
    "standard": {
        "transcript": True,
        "snapshots": False,
        "turns": True,
        "dead_air": True,
    },
    # The intent the issue names outright: nothing the caller said.
    "timing_only": {
        "transcript": False,
        "snapshots": False,
        "turns": True,
        "dead_air": True,
    },
    # timing_only minus the standing per-session cost. Dead air is the only
    # capture that runs a task for the life of every call.
    "lean": {
        "transcript": False,
        "snapshots": False,
        "turns": True,
        "dead_air": False,
    },
    # Everything, including the operator's own prompt and tool payloads. Named
    # `debug` rather than `full` because that is when it is worth the
    # disclosure, and the name should say so at the call site.
    "debug": {
        "transcript": True,
        "snapshots": True,
        "turns": True,
        "dead_air": True,
    },
    # Meter cost and latency, capture nothing else.
    "off": {
        "transcript": False,
        "snapshots": False,
        "turns": False,
        "dead_air": False,
    },
}

#: The switch names a policy sets, in a stable order for error messages.
CAPTURE_SWITCHES: Final[tuple[str, ...]] = (
    "transcript",
    "snapshots",
    "turns",
    "dead_air",
)


def resolve_policy(name: str | None) -> dict[str, bool]:
    """The four switches a policy sets, or the standard set when unnamed.

    An unknown name is REFUSED rather than falling back to a default. A typo
    that silently selected ``standard`` would turn "nothing the caller said"
    into transcript capture, which is the exact wrong-combination-nobody-decided
    this whole change exists to prevent.
    """
    if name is None:
        return dict(CAPTURE_POLICIES["standard"])
    try:
        return dict(CAPTURE_POLICIES[name])
    except KeyError:
        known = ", ".join(sorted(CAPTURE_POLICIES))
        raise ValueError(
            f"unknown capture policy {name!r}. Known policies: {known}."
        ) from None


__all__ = ["CAPTURE_POLICIES", "CAPTURE_SWITCHES", "resolve_policy"]
