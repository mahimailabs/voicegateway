"""Storage internals are private in fact, not just in name.

Before this wave, 63 call sites across 12 production modules reached through
``storage._conn`` and ``storage._ensure_initialized()``, and 24 route handlers
opened their own database session. Nothing could sit between a route and the
database, which is exactly where a tenant guard has to live: VG-SEC-001 was
one writer preferring a payload tenant because no single place decided
tenancy for every write.

The first test is red until Task 13 retires the last reach-through, so it
carries a strict xfail until then. That marker is the acceptance test for the
whole seam: when it XPASSes, the boundary exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
#: The one module allowed to touch the connection it owns.
_ALLOWED = {_SRC / "services" / "storage_service.py"}
_PATTERN = re.compile(r"\._conn\b|_ensure_initialized\(\)")


def _production_files() -> list[Path]:
    """Every shipped module. Tests may reach in; production may not."""
    return [p for p in sorted(_SRC.rglob("*.py")) if "tests" not in p.parts]


@pytest.mark.xfail(
    strict=True,
    reason="closes in Task 13, when the last reach-through is retired",
)
def test_no_module_outside_storage_service_touches_internals():
    offenders = []
    for path in _production_files():
        if path in _ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PATTERN.search(line):
                offenders.append(f"{path.relative_to(_SRC)}:{lineno}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_storage_service_exposes_a_public_session():
    """The one public path to a session. Routes get it via get_session."""
    from voicegateway.services.storage_service import StorageService

    assert callable(getattr(StorageService, "session", None))
