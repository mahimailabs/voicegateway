"""Guards for the two silent schema failures.

Both of these fail quietly today: a forked alembic chain and a model that was
never re-exported from ``models/__init__.py`` both pass every existing check and
only surface at deploy time. These tests are prerequisites for any autonomous
work on the schema.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import SQLModel

import voicegateway.models  # noqa: F401  (import registers every re-exported model)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODELS_DIR = Path(voicegateway.models.__file__).parent


def test_single_alembic_head() -> None:
    """The revision chain must never fork.

    The current head is itself a two-parent merge, so a new revision written
    against the wrong ``down_revision`` creates a second head. Alembic does not
    complain until someone runs ``upgrade head``.
    """
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()

    assert len(heads) == 1, (
        f"alembic has {len(heads)} heads: {heads}. "
        "Every new revision must chain linearly; do not create a merge revision."
    )


def _table_backed_classes(path: Path) -> list[str]:
    """Class names in ``path`` declared with ``table=True``, via AST (no import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "table"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                found.append(node.name)
    return found


def test_all_models_registered() -> None:
    """Every ``table=True`` model must reach ``SQLModel.metadata``.

    Registration happens only as a side effect of the class being imported, and
    ``models/__init__.py`` is what does that importing. Omit a re-export and the
    table is invisible to ``create_all`` *and* to autogenerate -- with no error.
    """
    registered = {
        cls.__name__
        for cls in SQLModel._sa_registry._class_registry.values()  # type: ignore[attr-defined]
        if hasattr(cls, "__tablename__")
    }

    missing: list[str] = []
    for module_path in sorted(_MODELS_DIR.glob("*_model.py")):
        for cls_name in _table_backed_classes(module_path):
            if cls_name not in registered:
                missing.append(f"{module_path.name}::{cls_name}")

    assert not missing, (
        "These table=True models never reached SQLModel.metadata: "
        f"{missing}. Add them to src/voicegateway/models/__init__.py -- "
        "without the re-export, alembic autogenerate silently skips the table."
    )


@pytest.mark.parametrize("required", ["Request", "Session"])
def test_guard_itself_detects_known_models(required: str) -> None:
    """Sanity check: the registry probe finds models we know are wired up.

    Without this, a bug in the introspection above would make
    ``test_all_models_registered`` vacuously pass.
    """
    registered = {
        cls.__name__
        for cls in SQLModel._sa_registry._class_registry.values()  # type: ignore[attr-defined]
        if hasattr(cls, "__tablename__")
    }
    assert required in registered, (
        f"{required} not found in SQLModel.metadata -- the registry introspection "
        "in this module is broken, so test_all_models_registered cannot be trusted."
    )
