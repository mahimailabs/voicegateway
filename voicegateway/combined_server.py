"""Deprecated import path: use ``voicegateway.server.combined`` instead.

v0.1.2 moved this module to ``voicegateway/server/combined.py`` as
part of the project-polish refactor (REQ-VG-POLISH-004). This file is
a re-export shim preserving the v0.1.x import paths
``from voicegateway.combined_server import build_combined_app, main``
and ``python -m voicegateway.combined_server``. The Refinery flags
shims as temporary scaffolding targeted for removal in v0.2.0 or
later (not v0.1.2).
"""

from voicegateway.server.combined import build_combined_app, main

__all__ = ["build_combined_app", "main"]


if __name__ == "__main__":
    main()
