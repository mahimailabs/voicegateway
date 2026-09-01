"""Fresh-process route snapshot for the Wave 0 authorization matrix tests."""

from __future__ import annotations

import json

from voicegateway.tests.server._telemetry_harness import _Harness, live_route_auth


def main() -> None:
    """Emit the current app's authorization graph as JSON."""
    harness = _Harness()
    try:
        snapshot = [
            [method, path, auth]
            for (method, path), auth in sorted(live_route_auth(harness.app).items())
        ]
        print(json.dumps(snapshot))
    finally:
        harness.cleanup()


if __name__ == "__main__":
    main()
