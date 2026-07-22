"""Resolve LiveKit server credentials for the diagnostic commands.

Order: explicit flags, then LIVEKIT_* env (the names the lk CLI uses), then a
livekit: block in voicegw.yaml. Never guesses a localhost default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class CredsError(Exception):
    """No usable LiveKit credentials were found."""


@dataclass(frozen=True)
class LiveKitCreds:
    url: str
    api_key: str
    api_secret: str


def _from_yaml(config_path: str | None) -> dict:
    # Precedence: explicit path, then $VOICEGW_CONFIG (the daemon publishes the
    # served -c path here), then ./voicegw.yaml, then the standard config home
    # (~/.config/voicegateway/voicegw.yaml) so the resolver still finds creds
    # when the daemon runs with a home working directory.
    candidates = [
        config_path,
        os.environ.get("VOICEGW_CONFIG"),
        "voicegw.yaml",
        os.path.expanduser("~/.config/voicegateway/voicegw.yaml"),
    ]
    path = next((c for c in candidates if c and os.path.exists(c)), None)
    if path is None:
        return {}
    import yaml

    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    block = data.get("livekit")
    return block if isinstance(block, dict) else {}


def resolve_creds(
    url: str | None,
    api_key: str | None,
    api_secret: str | None,
    config_path: str | None = None,
) -> LiveKitCreds:
    yaml_block = None

    def pick(flag: str | None, env: str, key: str) -> str | None:
        nonlocal yaml_block
        if flag:
            return flag
        if os.environ.get(env):
            return os.environ[env]
        if yaml_block is None:
            yaml_block = _from_yaml(config_path)
        value = yaml_block.get(key)
        return str(value) if value else None

    resolved_url = pick(url, "LIVEKIT_URL", "url")
    resolved_key = pick(api_key, "LIVEKIT_API_KEY", "api_key")
    resolved_secret = pick(api_secret, "LIVEKIT_API_SECRET", "api_secret")
    missing = [
        name
        for name, val in (
            ("url", resolved_url),
            ("api_key", resolved_key),
            ("api_secret", resolved_secret),
        )
        if not val
    ]
    if missing:
        raise CredsError(
            "missing LiveKit credentials: "
            + ", ".join(missing)
            + ". Pass --url/--api-key/--api-secret, set LIVEKIT_URL/"
            "LIVEKIT_API_KEY/LIVEKIT_API_SECRET, or add a livekit: block to "
            "voicegw.yaml."
        )
    # All three are non-empty here (missing would be non-empty otherwise); assert
    # it so the type checker narrows str | None to str.
    assert resolved_url and resolved_key and resolved_secret
    return LiveKitCreds(resolved_url, resolved_key, resolved_secret)
