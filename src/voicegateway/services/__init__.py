"""Side-effect-light orchestration that sits above :mod:`voicegateway.repository`.

A service composes one or more repositories plus pure-Python policy
(budget checks, fallback decisions, reconciliation arithmetic) into a
unit a caller can invoke without knowing the storage shape.
:class:`voicegateway.core.gateway.Gateway` is currently a god-service
that owns this orchestration; services live here as it's decomposed.
"""

__all__: list[str] = []
