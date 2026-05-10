"""VoiceGateway CLI package.

Replaces the v0.0.5 single-file ``voicegateway/cli.py``. The contract
``from voicegateway.cli import app`` is preserved verbatim so existing
scripts, the ``voicegw = "voicegateway.cli:app"`` console-script entry
point in pyproject.toml, and external imports keep working.

Layout:

- ``_app.py`` — the shared Typer ``app`` instance, the Rich
  ``console``, and the global ``--version`` callback.
- ``_helpers.py`` — cross-command helpers (``_load_gateway``,
  ``_parse_iso_date_arg``).
- one submodule per command: ``init``, ``rotate_secret``, ``status``,
  ``costs``, ``projects`` (owns both the ``projects`` listing and
  the ``project`` detail), ``logs``, ``smoke_test``, ``serve``,
  ``dashboard``, ``export_costs``, ``reconcile``, ``mcp``.

Importing this package side-effect-imports each command submodule.
Each submodule's ``@app.command(...)`` decorator runs at import time
and registers the command on the shared ``app``. Order is alphabetical
because isort imposes it; commands carry their own names so order
does not matter.

v0.1.0 sections 3-6 add more submodules (the daemon backends, the
``onboard`` wizard, the ``lifecycle`` group, the ``doctor`` command,
the ``migrate`` command). They follow the same shape: one submodule,
side-effect import added here.
"""

from __future__ import annotations

# Side-effect imports: each submodule registers its commands on the
# shared ``app`` via ``@app.command(...)`` decorators that run at
# import time. Order does not matter; commands carry their own names.
from voicegateway.cli import costs as _costs  # noqa: F401, E402
from voicegateway.cli import dashboard as _dashboard  # noqa: F401, E402
from voicegateway.cli import doctor as _doctor  # noqa: F401, E402
from voicegateway.cli import export_costs as _export_costs  # noqa: F401, E402
from voicegateway.cli import init as _init  # noqa: F401, E402
from voicegateway.cli import lifecycle as _lifecycle  # noqa: F401, E402
from voicegateway.cli import logs as _logs  # noqa: F401, E402
from voicegateway.cli import mcp as _mcp  # noqa: F401, E402
from voicegateway.cli import migrate as _migrate  # noqa: F401, E402
from voicegateway.cli import onboard as _onboard  # noqa: F401, E402
from voicegateway.cli import projects as _projects  # noqa: F401, E402
from voicegateway.cli import reconcile as _reconcile  # noqa: F401, E402
from voicegateway.cli import rotate_secret as _rotate_secret  # noqa: F401, E402
from voicegateway.cli import serve as _serve  # noqa: F401, E402
from voicegateway.cli import smoke_test as _smoke_test  # noqa: F401, E402
from voicegateway.cli import status as _status  # noqa: F401, E402
from voicegateway.cli import tui as _tui  # noqa: F401, E402

# Single source of truth for both ``app`` (the Typer instance every
# command registers on) and ``console`` (the Rich Console used for
# uniform terminal output across commands and submodules added later).
# Re-exported via ``__all__`` so ``from voicegateway.cli import *``
# only sees the documented public surface.
from voicegateway.cli._app import app, console

__all__ = ["app", "console"]
