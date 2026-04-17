"""Optional bearer token authentication for the MCP HTTP/SSE transport.

Enabled only when the ``VOICEGW_MCP_TOKEN`` environment variable is set.
stdio transport never checks auth.
"""

from __future__ import annotations

import hmac
import os


class AuthError(Exception):
    """Raised when a request is missing or has an invalid bearer token."""

    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_expected_token() -> str | None:
    """Return the configured token, or None if auth is disabled."""
    return os.environ.get("VOICEGW_MCP_TOKEN")


def check_authorization_header(authorization: str | None) -> None:
    """Validate a raw ``Authorization`` header value.

    Raises AuthError if auth is enabled and the header is missing or invalid.
    Passes silently when auth is disabled.
    """
    expected_token = get_expected_token()
    if not expected_token:
        return

    if not authorization:
        raise AuthError("Missing bearer token")

    if not authorization.startswith("Bearer "):
        raise AuthError("Missing bearer token")

    provided_token = authorization[len("Bearer "):]
    if not hmac.compare_digest(provided_token, expected_token):
        raise AuthError("Invalid token")
