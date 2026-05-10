"""VoiceGateway CLI package.

Replaces the v0.0.5 single-file ``voicegateway/cli.py``. The contract
``from voicegateway.cli import app`` is preserved verbatim so existing
scripts, the ``voicegw = "voicegateway.cli:app"`` console-script entry
point in pyproject.toml, and external imports keep working.

During the v0.1.0 refactor the original module body lives in
``_legacy.py``. Subsequent v0.1.0 commits carve individual commands
(``init``, ``serve``, ``onboard``, ``doctor``, ``migrate``, the
lifecycle group, etc.) out of ``_legacy.py`` into focused submodules.
When ``_legacy.py`` no longer holds any commands it is deleted and
this docstring is rewritten to describe the final layout.

Importing this module triggers ``_legacy.py`` once, which registers
every Typer command on the shared ``app`` instance. The carve-out
commits each move one ``@app.command(...)`` block out of ``_legacy.py``
into a new submodule and update ``__init__.py`` to import that
submodule for its side effect (the ``@app.command`` decorator runs
at import time).
"""

from __future__ import annotations

# Side-effect imports: each submodule registers its commands on the
# shared ``app`` via ``@app.command(...)`` decorators that run at
# import time. Order does not matter; commands carry their own names.
from voicegateway.cli import costs as _costs  # noqa: F401, E402
from voicegateway.cli import dashboard as _dashboard  # noqa: F401, E402
from voicegateway.cli import export_costs as _export_costs  # noqa: F401, E402
from voicegateway.cli import init as _init  # noqa: F401, E402
from voicegateway.cli import logs as _logs  # noqa: F401, E402
from voicegateway.cli import mcp as _mcp  # noqa: F401, E402
from voicegateway.cli import projects as _projects  # noqa: F401, E402
from voicegateway.cli import reconcile as _reconcile  # noqa: F401, E402
from voicegateway.cli import rotate_secret as _rotate_secret  # noqa: F401, E402
from voicegateway.cli import serve as _serve  # noqa: F401, E402
from voicegateway.cli import smoke_test as _smoke_test  # noqa: F401, E402
from voicegateway.cli import status as _status  # noqa: F401, E402

# Single source of truth for both ``app`` (the Typer instance every
# command registers on) and ``console`` (the Rich Console used for
# uniform terminal output across commands and submodules added later).
# Re-exported via ``__all__`` so ``from voicegateway.cli import *``
# only sees the documented public surface.
from voicegateway.cli._legacy import app, console

__all__ = ["app", "console"]
