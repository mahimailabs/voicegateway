"""Contract test: every voicegateway subpackage declares __all__."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import pytest

import voicegateway

pytestmark = pytest.mark.integration


def _walk_subpackages() -> list[ModuleType]:
    """Yield every subpackage of ``voicegateway`` (including the root)."""
    found: list[ModuleType] = [voicegateway]
    for module_info in pkgutil.walk_packages(
        voicegateway.__path__, prefix="voicegateway."
    ):
        if not module_info.ispkg:
            continue
        if module_info.name == "voicegateway.tests" or module_info.name.startswith(
            "voicegateway.tests."
        ):
            continue
        found.append(importlib.import_module(module_info.name))
    return found


_SUBPACKAGES = _walk_subpackages()


# Sanity floor on subpackage count. The structural-refactor pass added
# ``voicegateway.models`` (entity dataclasses). If this drifts unexpectedly,
# it is a hint a new subpackage landed without being thought through.
def test_walker_finds_expected_number_of_subpackages() -> None:
    """Subpackage count is stable across the structural refactor."""
    assert len(_SUBPACKAGES) == 30, (
        f"Expected 30 subpackages (root + 29 nested), got "
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
    """``__all__`` must be a list (or tuple) of str, not anything else."""
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
    """Every name in ``__all__`` must resolve to a real attribute."""
    missing = [name for name in package.__all__ if not hasattr(package, name)]
    assert not missing, (
        f"{package.__name__}.__all__ lists names that do not resolve: "
        f"{missing}. Either remove the entries or add the corresponding "
        "re-export to the package __init__."
    )


@pytest.mark.parametrize("package", _SUBPACKAGES, ids=lambda p: p.__name__)
def test_subpackage_all_has_no_private_names(package: ModuleType) -> None:
    """``__all__`` should not list names starting with underscore."""
    private = [name for name in package.__all__ if name.startswith("_")]
    # ``__version__`` and other dunder names are not "private" in this
    # sense; only single-underscore prefixes.
    private = [n for n in private if not (n.startswith("__") and n.endswith("__"))]
    assert not private, (
        f"{package.__name__}.__all__ exposes underscore-prefixed names: "
        f"{private}. Either drop them from __all__ or rename them."
    )
