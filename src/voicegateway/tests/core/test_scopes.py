"""core.scopes is the runtime vocabulary; ScopeName mirrors it. They agree.

``core.scopes`` is what production imports at runtime, and it has to be a
leaf: ``repository`` imports it, so if it imported back into ``repository``
or ``core.auth`` the import cycle it exists to break would simply move.
"""

from __future__ import annotations

import ast
from pathlib import Path

from voicegateway.core import scopes
from voicegateway.schemas.telemetry.security_schema import ScopeName


def test_runtime_scopes_match_the_contract_enum():
    """A scope that exists at runtime but not in the contract is invisible."""
    assert scopes.ALL == {s.value for s in ScopeName}


def test_scopes_module_imports_nothing():
    """A leaf module: repository imports it, so it may import no package."""
    source = Path(scopes.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imports == [] or all(
        isinstance(n, ast.ImportFrom) and n.module == "__future__" for n in imports
    )
