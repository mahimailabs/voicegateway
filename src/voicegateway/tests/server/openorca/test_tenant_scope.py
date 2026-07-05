"""_event_for_subscriber: per-tenant SSE fan-out scoping.

A producer tags a tenant-owned event with an internal ``_tenant`` key. The SSE
stream must never deliver one tenant's event to another tenant's subscriber,
and must strip the internal key before any event reaches a client. An untagged
event (``_tenant`` absent or ``None``) is a broadcast delivered to everyone.
"""

from __future__ import annotations

from voicegateway.server.api.openorca.routes import _event_for_subscriber


def test_foreign_tenant_event_is_dropped() -> None:
    event = {"type": "fleet.updated", "fleetHealth": {}, "_tenant": "acme"}
    assert _event_for_subscriber(event, "beta") is None


def test_matching_tenant_event_is_delivered_without_internal_key() -> None:
    event = {"type": "fleet.updated", "fleetHealth": {}, "_tenant": "acme"}
    visible = _event_for_subscriber(event, "acme")
    assert visible == {"type": "fleet.updated", "fleetHealth": {}}
    assert "_tenant" not in visible


def test_none_tagged_event_is_delivered_to_any_subscriber() -> None:
    event = {"type": "fleet.updated", "fleetHealth": {}, "_tenant": None}
    visible = _event_for_subscriber(event, "beta")
    assert visible == {"type": "fleet.updated", "fleetHealth": {}}
    assert "_tenant" not in visible


def test_untagged_event_is_delivered_to_any_subscriber() -> None:
    event = {"type": "fleet.updated", "fleetHealth": {}}
    assert _event_for_subscriber(event, "beta") == event
