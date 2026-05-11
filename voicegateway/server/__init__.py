"""HTTP API server for VoiceGateway.

v0.1.2 split the original voicegateway/server.py into this subpackage
so the top-level voicegateway/ tree contains only subpackages plus
__init__.py and _version.py (REQ-VG-POLISH-004). The implementation
lives in voicegateway/server/main.py; this __init__ re-exports
`build_app` so the v0.1.x public import path
`from voicegateway.server import build_app` keeps working
(REQ-VG-POLISH-004 AC-2).
"""

from voicegateway.server.main import build_app

__all__ = ["build_app"]
