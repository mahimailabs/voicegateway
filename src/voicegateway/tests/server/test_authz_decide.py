"""decide() is the only place the enforcement mode is consulted.

Every gate computes whether it *would* refuse, then asks decide() what to do
about that. No gate branches on the mode itself, which is what makes flipping
0.27.0 to enforce a default change rather than a code change, and what stops
one gate quietly disagreeing with another about what warn means.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request

from voicegateway.core.config import AuthConfig
from voicegateway.schemas.telemetry.security_schema import PrincipalKind
from voicegateway.server.api._authz import WOULD_REFUSE_EVENT, Decision, decide


def _request(path: str = "/v1/costs", method: str = "GET") -> Request:
    """A minimal ASGI request bound to a throwaway app for its state."""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "app": FastAPI(),
        }
    )


@pytest.mark.parametrize("mode", ["warn", "enforce"])
def test_no_refusal_is_allow_in_every_mode(mode):
    decision = decide(
        would_refuse=False,
        reason="",
        auth=AuthConfig(enforcement=mode),
        request=_request(),
        principal_kind=PrincipalKind.OPERATOR,
        key_id=None,
    )
    assert decision is Decision.ALLOW


def test_local_development_allows_silently(caplog):
    """Local mode is a deliberate choice, so it is not nagged about."""
    request = _request()
    with caplog.at_level(logging.WARNING):
        decision = decide(
            would_refuse=True,
            reason="no credential",
            auth=AuthConfig(local_development=True),
            request=request,
            principal_kind=PrincipalKind.OPERATOR,
            key_id=None,
        )
    assert decision is Decision.ALLOW
    assert WOULD_REFUSE_EVENT not in caplog.text
    assert getattr(request.app.state, "auth_would_refuse", 0) == 0


def test_warn_mode_allows_logs_and_counts(caplog):
    """The point of warn: serve it, but say exactly what would have broken."""
    request = _request()
    with caplog.at_level(logging.WARNING):
        decision = decide(
            would_refuse=True,
            reason="no credential and no api_keys configured",
            auth=AuthConfig(enforcement="warn"),
            request=request,
            principal_kind=PrincipalKind.OPERATOR,
            key_id=None,
        )
    assert decision is Decision.WARN
    assert WOULD_REFUSE_EVENT in caplog.text
    assert "/v1/costs" in caplog.text
    assert "no credential and no api_keys configured" in caplog.text
    assert request.app.state.auth_would_refuse == 1


def test_warn_counter_accumulates_across_requests():
    """The counter is what an operator watches before flipping the mode."""
    request = _request()
    for _ in range(3):
        decide(
            would_refuse=True,
            reason="no credential",
            auth=AuthConfig(enforcement="warn"),
            request=request,
            principal_kind=PrincipalKind.OPERATOR,
            key_id=None,
        )
    assert request.app.state.auth_would_refuse == 3


def test_warn_names_the_key_when_one_authenticated(caplog):
    """ "Something is unauthorized" is useless; the key id is actionable."""
    with caplog.at_level(logging.WARNING):
        decide(
            would_refuse=True,
            reason="missing scope ingest",
            auth=AuthConfig(enforcement="warn"),
            request=_request("/v1/ingest", "POST"),
            principal_kind=PrincipalKind.TENANT_KEY,
            key_id=42,
        )
    assert "key_id=42" in caplog.text
    assert "tenant_key" in caplog.text
    assert "POST" in caplog.text


def test_enforce_mode_refuses_without_logging_a_warning(caplog):
    """Under enforce the refusal is the signal; a warning would be noise."""
    request = _request()
    with caplog.at_level(logging.WARNING):
        decision = decide(
            would_refuse=True,
            reason="no credential",
            auth=AuthConfig(enforcement="enforce"),
            request=request,
            principal_kind=PrincipalKind.OPERATOR,
            key_id=None,
        )
    assert decision is Decision.REFUSE
    assert WOULD_REFUSE_EVENT not in caplog.text
    assert getattr(request.app.state, "auth_would_refuse", 0) == 0
