"""Contract test: every voicegateway subpackage declares __all__.

REQ-VG-POLISH-006 AC-1 ("an __all__ list declares the symbols intended
for external import") becomes a runtime contract here: walk every
subpackage under ``voicegateway/``, assert it has ``__all__``, and
assert every name in ``__all__`` resolves to a real attribute on the
package.

If a contributor adds a new subpackage without ``__all__``, this test
fails. If a contributor adds a name to ``__all__`` but forgets to
re-export it, this test fails. Both are exactly the regressions
REQ-006 is designed to prevent.

Scope: package ``__init__.py`` files only. Submodules
(e.g. ``voicegateway.core.gateway``) are NOT required to have
``__all__``; the contract is about the package-level public surface.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import pytest

import voicegateway


def _walk_subpackages() -> list[ModuleType]:
    """Yield every subpackage of ``voicegateway`` (including the root).

    Uses :func:`pkgutil.walk_packages` filtered to ``ispkg``. The root
    ``voicegateway`` itself is yielded first because ``walk_packages``
    starts one level down.
    """
    found: list[ModuleType] = [voicegateway]
    for module_info in pkgutil.walk_packages(
        voicegateway.__path__, prefix="voicegateway."
    ):
        if module_info.ispkg:
            found.append(importlib.import_module(module_info.name))
    return found


_SUBPACKAGES = _walk_subpackages()


# Sanity floor: the audit in v0.1.2's T13 found 18 ``__init__.py`` files
# under ``voicegateway/``. v0.2.0's T04 added one
# (``voicegateway/storage/migrations/``), so the post-v0.2.0-T04 count is
# 19 (root + 18 nested). If this count drifts unexpectedly, that is a
# hint a new subpackage landed without being thought through.
def test_walker_finds_expected_number_of_subpackages() -> None:
    """Subpackage count is stable post-v0.2.0-T04."""
    assert len(_SUBPACKAGES) == 19, (
        f"Expected 19 subpackages (root + 18 nested), got "
        f"{len(_SUBPACKAGES)}: "
        f"{sorted(p.__name__ for p in _SUBPACKAGES)}"
    )


@pytest.mark.parametrize("package", _SUBPACKAGES, ids=lambda p: p.__name__)
def test_subpackage_declares_all(package: ModuleType) -> None:
    """Every subpackage's ``__init__.py`` must declare ``__all__``."""
    assert hasattr(package, "__all__"), (
        f"{package.__name__} is missing __all__. Every subpackage "
        "must declare its public surface explicitly "
        "(REQ-VG-POLISH-006 AC-1). Use __all__: list[str] = [] if the "
        "subpackage re-exports nothing at the top level."
    )


@pytest.mark.parametrize("package", _SUBPACKAGES, ids=lambda p: p.__name__)
def test_subpackage_all_is_list_of_str(package: ModuleType) -> None:
    """``__all__`` must be a list (or tuple) of str, not anything else.

    Static analyzers and ``from X import *`` both rely on the standard
    shape; an accidentally-set ``__all__ = "build_app"`` (a single
    string) would silently degrade to character-by-character iteration.
    """
    all_attr = package.__all__
    assert isinstance(all_attr, (list, tuple)), (
        f"{package.__name__}.__all__ must be list or tuple, "
        f"got {type(all_attr).__name__}"
    )
    for name in all_attr:
        assert isinstance(name, str), (
            f"{package.__name__}.__all__ contains non-str entry "
            f"{name!r} ({type(name).__name__})"
        )


@pytest.mark.parametrize("package", _SUBPACKAGES, ids=lambda p: p.__name__)
def test_subpackage_all_names_resolve(package: ModuleType) -> None:
    """Every name in ``__all__`` must resolve to a real attribute.

    Catches the regression of: adding a re-export to ``__all__`` but
    forgetting the actual ``from .x import y`` line above it. The
    package would still import, but ``from package import y`` would
    fail with AttributeError.
    """
    missing = [name for name in package.__all__ if not hasattr(package, name)]
    assert not missing, (
        f"{package.__name__}.__all__ lists names that do not resolve: "
        f"{missing}. Either remove the entries or add the corresponding "
        "re-export to the package __init__."
    )


@pytest.mark.parametrize("package", _SUBPACKAGES, ids=lambda p: p.__name__)
def test_subpackage_all_has_no_private_names(package: ModuleType) -> None:
    """``__all__`` should not list names starting with underscore.

    Leading-underscore names are by-convention private. Listing them
    in ``__all__`` confuses the public-API contract documented in
    ``docs/contributing/code-style.md``.
    """
    private = [name for name in package.__all__ if name.startswith("_")]
    # ``__version__`` and other dunder names are not "private" in this
    # sense; only single-underscore prefixes.
    private = [n for n in private if not (n.startswith("__") and n.endswith("__"))]
    assert not private, (
        f"{package.__name__}.__all__ exposes underscore-prefixed names: "
        f"{private}. Either drop them from __all__ or rename them."
    )
