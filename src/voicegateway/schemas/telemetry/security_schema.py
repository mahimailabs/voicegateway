"""Wave 0 security contract vocabulary: enums, rows, content states, envelope.

This module is deliberately inert. It declares the shapes that Wave 1 and
later waves must satisfy, and it imports nothing from ``voicegateway.server``,
``voicegateway.repository`` or ``voicegateway.telemetry``. Nothing here is
wired into a running request path.

Five concerns share one module because they share one vocabulary
(``gap_id``, :class:`ContractStatus`, :class:`ScopeName`, :class:`ThreatId`,
:class:`PrincipalKind`). ``base_schema.py`` is the in-repo precedent for a
multi-concept schema module.

- the authorization matrix row type plus its loader,
- the threat model row type plus its loader,
- :class:`ContentState` and its one-way transition map,
- the encryption envelope interface (no backend chosen, no implementation),
- :class:`SensitiveAccessEvent`, the audit record for reads of content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from importlib import resources
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

#: Package that carries the two JSON data files, resolved via
#: ``importlib.resources`` exactly like ``provider_baselines.json``.
_DATA_PACKAGE = "voicegateway.schemas.telemetry"

#: ``VG-SEC-001`` .. ``VG-SEC-014``. Minted once in ``threat_model.json`` and
#: repeated verbatim in the matrix, the fixtures, the tests and the docs page.
GAP_ID_PATTERN = r"^VG-SEC-\d{3}$"

GapId = Annotated[str, Field(pattern=GAP_ID_PATTERN)]


class ContractStatus(StrEnum):
    """How a contract row relates to what production does today."""

    #: Production already meets the row. Probed by a conformance test.
    ENFORCED = "enforced"
    #: The surface exists and production deviates from the contract.
    #: Carries a characterization test plus a strict-xfail contract test.
    GAP = "gap"
    #: No production surface exists yet. Carries an absence guard.
    PLANNED = "planned"
    #: Production now meets the row. Threat-model entries only: the entry
    #: stays as history and keeps its evidence line, while the matrix row
    #: for the same route becomes ENFORCED with no gap id.
    CLOSED = "closed"


class ScopeName(StrEnum):
    """Scope names: the two that exist today and the four Wave 1 targets."""

    #: Exists. Covers telemetry ingest AND config mutation across 18 routes,
    #: which is why splitting it is semantic rather than a rename.
    WRITE = "write"
    #: Exists as ``READ_SCOPE`` but is consulted only on the static-key branch.
    READ = "read"
    #: Exists, and is checked two ways (``role`` column and ``scopes`` CSV).
    ADMIN = "admin"
    #: Matches every check. Every minted key defaults to this value.
    WILDCARD = "*"
    #: Target. Does not exist: ingest is gated by ``write`` today.
    INGEST = "ingest"
    #: Target. Does not exist: MCP auth is one shared static token.
    MCP_READ = "mcp:read"


#: The scopes an unmodified checkout can actually enforce right now.
EXISTING_SCOPES: frozenset[ScopeName] = frozenset(
    {ScopeName.WRITE, ScopeName.READ, ScopeName.ADMIN, ScopeName.WILDCARD}
)

#: The scopes Wave 1 must introduce. Asserted absent by the absence guards.
PLANNED_SCOPES: frozenset[ScopeName] = frozenset({ScopeName.INGEST, ScopeName.MCP_READ})


class PrincipalKind(StrEnum):
    """The kinds of caller the auth layer can resolve."""

    #: No credential, or a static config key. Resolves to a full admin.
    OPERATOR = "operator"
    #: A ``vk_`` key whose ``role`` is ``admin``. May span tenants.
    ADMIN_KEY = "admin_key"
    #: A ``vk_`` key whose ``role`` is ``tenant``. Bound to one tenant.
    TENANT_KEY = "tenant_key"
    #: A token listed in ``auth.api_keys`` in the YAML config.
    STATIC_KEY = "static_key"
    #: The shared ``VOICEGW_MCP_TOKEN``. Carries no per-caller identity.
    MCP_TOKEN = "mcp_token"


class ThreatId(StrEnum):
    """The seven threats the Wave 0 model enumerates."""

    CROSS_TENANT_WRITE = "VG-THREAT-001"
    CROSS_TENANT_READ = "VG-THREAT-002"
    MISSING_PROJECT_AUTHZ = "VG-THREAT-003"
    COARSE_SCOPES = "VG-THREAT-004"
    INGEST_EXHAUSTION = "VG-THREAT-005"
    CONTENT_EXPOSURE = "VG-THREAT-006"
    AUDIT_BLINDNESS = "VG-THREAT-007"


class RouteAuth(StrEnum):
    """How a route is gated, as recovered from the FastAPI dependency graph."""

    #: No auth dependency resolves on this route.
    OPEN = "open"
    #: Guarded by ``require_principal``.
    PRINCIPAL = "principal"
    #: Guarded by ``require_scope("write")``.
    SCOPE_WRITE = "scope:write"
    #: Guarded by ``require_scope("admin")``.
    SCOPE_ADMIN = "scope:admin"
    #: Guarded by ``require_ingest_principal``, which enforces the ingest
    #: scope and yields the Principal the handler writes rows under.
    SCOPE_INGEST = "scope:ingest"


class AuthorizationRule(BaseModel):
    """One ``(method, path)`` row of the authorization matrix.

    ``status`` here answers a narrower question than it does on
    :class:`ThreatEntry`: *does this route, as shipped, meet its contract?*
    Only ``ENFORCED`` and ``GAP`` are legal, because a row exists if and only
    if the route exists. A contract for a route that has not been written yet
    is a :class:`PlannedRoute`, so ``PLANNED`` never appears here and the enum
    is never overloaded within one file.

    A ``GAP`` row must name a ``gap_id`` and a ``wave``, which is what stops a
    row from being aspirational prose with no owner.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"]
    path: str
    auth: RouteAuth
    status: ContractStatus
    #: True when the row's contract requires the response be filtered to the
    #: caller's tenant. False for genuinely global routes such as ``/health``.
    tenant_scoped: bool
    gap_id: GapId | None = None
    wave: int | None = Field(default=None, ge=1)
    note: str = ""

    @model_validator(mode="after")
    def _gap_rows_are_owned(self) -> AuthorizationRule:
        """A non-enforced row must name the gap and the wave that closes it."""
        if self.status is ContractStatus.PLANNED:
            raise ValueError(
                f"{self.method} {self.path}: a matrix row describes a route "
                "that exists; use planned_routes for one that does not"
            )
        if self.status is ContractStatus.CLOSED:
            raise ValueError(
                f"{self.method} {self.path}: CLOSED belongs to the threat "
                "model, which keeps a gap as history; the row for a closed "
                "gap becomes ENFORCED with no gap_id"
            )
        if self.status is ContractStatus.ENFORCED:
            if self.gap_id is not None or self.wave is not None:
                raise ValueError(
                    f"{self.method} {self.path}: an enforced row carries no "
                    "gap_id and no wave"
                )
            return self
        if self.gap_id is None or self.wave is None:
            raise ValueError(
                f"{self.method} {self.path}: status={self.status.value} "
                "requires both gap_id and wave"
            )
        return self

    @property
    def key(self) -> tuple[str, str]:
        """The ``(method, path)`` identity used for the bijection test."""
        return (self.method, self.path)


class PlannedRoute(BaseModel):
    """A route that does not exist yet, with the contract it must ship under.

    Kept apart from :attr:`AuthorizationMatrix.routes` so the bijection test
    stays exact. Its test shape is the absence guard: the path must genuinely
    not be servable today, which turns "planned" into something falsifiable
    rather than a promise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"]
    path: str
    gap_id: GapId
    wave: int = Field(ge=1)
    #: The scope this route must require the day it is written.
    required_scope: ScopeName
    #: Where the tenant on a written row must come from. ``server_derived``
    #: means the authenticated principal wins and any tenant in the payload is
    #: discarded, never merged. This is a field rather than a sentence in a
    #: note because VG-SEC-001 is what a prose-only version of this rule looks
    #: like after it has been ignored once: the guarantee was written down in
    #: docs/architecture/security.md and the code did the opposite.
    tenant_source: Literal["server_derived", "not_applicable"]
    note: str = ""

    @model_validator(mode="after")
    def _writes_must_derive_their_tenant(self) -> PlannedRoute:
        """Any planned route that ingests rows must derive its own tenant."""
        if self.required_scope is ScopeName.INGEST and (
            self.tenant_source != "server_derived"
        ):
            raise ValueError(
                f"{self.method} {self.path}: an ingest route writes rows, so "
                "tenant_source must be 'server_derived' (see VG-SEC-015)"
            )
        return self

    @property
    def key(self) -> tuple[str, str]:
        """The ``(method, path)`` identity, matching :class:`AuthorizationRule`."""
        return (self.method, self.path)


class AuthorizationMatrix(BaseModel):
    """Exactly one row per live API route, plus the routes Wave 1 will add."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: list[AuthorizationRule]
    planned_routes: list[PlannedRoute] = Field(default_factory=list)

    def keys(self) -> set[tuple[str, str]]:
        """Return every ``(method, path)`` covered by a live row."""
        return {rule.key for rule in self.routes}

    def planned_keys(self) -> set[tuple[str, str]]:
        """Return every ``(method, path)`` that must NOT exist yet."""
        return {planned.key for planned in self.planned_routes}

    def gap_ids(self) -> set[str]:
        """Return every gap id referenced by a live row or a planned route."""
        live = {r.gap_id for r in self.routes if r.gap_id is not None}
        return live | {p.gap_id for p in self.planned_routes}

    def by_key(self) -> dict[tuple[str, str], AuthorizationRule]:
        """Index the live rows by ``(method, path)``."""
        return {rule.key: rule for rule in self.routes}

    @model_validator(mode="after")
    def _keys_are_unique_and_disjoint(self) -> AuthorizationMatrix:
        """No duplicate rows, and nothing is both live and planned."""
        live = [r.key for r in self.routes]
        if len(live) != len(set(live)):
            dupes = sorted({k for k in live if live.count(k) > 1})
            raise ValueError(f"duplicate matrix rows: {dupes}")
        planned = [p.key for p in self.planned_routes]
        if len(planned) != len(set(planned)):
            dupes = sorted({k for k in planned if planned.count(k) > 1})
            raise ValueError(f"duplicate planned routes: {dupes}")
        overlap = set(live) & set(planned)
        if overlap:
            raise ValueError(f"route is both live and planned: {sorted(overlap)}")
        return self


class ThreatEntry(BaseModel):
    """One gap: what it is, which threat it serves, and where it lives."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_id: GapId
    threat: ThreatId
    title: str
    description: str
    status: ContractStatus
    wave: int = Field(ge=1)
    #: ``path/to/file.py:LINE`` into production source. The claim must be
    #: checkable by opening one file at one line.
    evidence: str = Field(pattern=r"^[\w./-]+\.py:\d+$")
    #: Fixture file that exercises this gap, relative to the fixture root.
    #: ``None`` where the gap has no request-shaped expression.
    fixture: str | None = None
    #: Release that closed the gap, e.g. ``0.26.0``. Required iff CLOSED.
    closed_in: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")
    #: Commit that closed it. Required iff CLOSED.
    closed_by: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")

    @model_validator(mode="after")
    def _status_and_closure_agree(self) -> ThreatEntry:
        """Enforced guarantees live in the matrix; closed gaps stay as history.

        A closed entry is never deleted: the docs page cites gap ids verbatim
        and the keystone test asserts the minted set is contiguous from
        VG-SEC-001, so removing one would break both. It keeps its evidence
        line and gains a receipt: which release closed it, and which commit.
        """
        if self.status is ContractStatus.ENFORCED:
            raise ValueError(
                f"{self.gap_id}: the threat model holds gap, planned and closed "
                "rows only; an enforced guarantee belongs in the matrix"
            )
        has_closure = self.closed_in is not None or self.closed_by is not None
        if self.status is ContractStatus.CLOSED:
            if self.closed_in is None or self.closed_by is None:
                raise ValueError(
                    f"{self.gap_id}: a closed entry needs closed_in and closed_by"
                )
            return self
        if has_closure:
            raise ValueError(
                f"{self.gap_id}: only a closed entry carries closed_in or closed_by"
            )
        return self


class ThreatModel(RootModel[list[ThreatEntry]]):
    """Every gap Wave 0 records, keyed by gap id."""

    @model_validator(mode="after")
    def _gap_ids_are_unique(self) -> ThreatModel:
        """Reject duplicate ids so an index cannot silently discard a gap."""
        gap_ids = [entry.gap_id for entry in self.root]
        duplicates = sorted({gap_id for gap_id in gap_ids if gap_ids.count(gap_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate threat-model gap ids: {duplicates}")
        return self

    def gap_ids(self) -> set[str]:
        """Return the full set of minted gap ids, closed ones included.

        Deliberately complete: the contiguity pin and the docs cross-reference
        both check against every id ever minted, not just the open ones.
        """
        return {entry.gap_id for entry in self.root}

    def open_gap_ids(self) -> set[str]:
        """Return the ids still open. This is the remaining work."""
        return {
            entry.gap_id
            for entry in self.root
            if entry.status is not ContractStatus.CLOSED
        }

    def by_gap_id(self) -> dict[str, ThreatEntry]:
        """Index the entries by gap id."""
        return {entry.gap_id: entry for entry in self.root}

    def by_threat(self, threat: ThreatId) -> list[ThreatEntry]:
        """Return every gap filed under ``threat``."""
        return [entry for entry in self.root if entry.threat is threat]


class ContentState(StrEnum):
    """Lifecycle of stored call content (transcripts, replay audio).

    The five states are the roadmap's frozen vocabulary; Waves 3 and 4
    enumerate them verbatim, so this module does not get to invent its own.
    :attr:`UNAVAILABLE` is the projection an unauthorized caller sees. It is
    deliberately indistinguishable from every other state, which is why
    :class:`ContentDescriptor` forbids byte counts and expiry on it.
    """

    #: Durable and retrievable by an authorized caller.
    CAPTURED = "captured"
    #: Content removed by policy, metadata retained deliberately.
    REDACTED = "redacted"
    #: Captured but cut at a size limit. The state records the cut, so a
    #: partial transcript is never served as though it were complete.
    TRUNCATED = "truncated"
    #: Content removed by retention. Metadata may survive.
    EXPIRED = "expired"
    #: The caller may not learn whether this content exists.
    UNAVAILABLE = "unavailable"


#: One-way lifecycle. Content never moves back toward a richer state, so an
#: expired recording can never be resurrected by a later ingest replaying an
#: older revision, and a truncation is never silently un-cut. Every state may
#: collapse to ``UNAVAILABLE`` because that is an authorization projection
#: rather than a storage event.
CONTENT_STATE_TRANSITIONS: Mapping[ContentState, frozenset[ContentState]] = {
    ContentState.CAPTURED: frozenset(
        {
            ContentState.TRUNCATED,
            ContentState.REDACTED,
            ContentState.EXPIRED,
            ContentState.UNAVAILABLE,
        }
    ),
    ContentState.TRUNCATED: frozenset(
        {ContentState.REDACTED, ContentState.EXPIRED, ContentState.UNAVAILABLE}
    ),
    ContentState.REDACTED: frozenset({ContentState.EXPIRED, ContentState.UNAVAILABLE}),
    ContentState.EXPIRED: frozenset({ContentState.UNAVAILABLE}),
    ContentState.UNAVAILABLE: frozenset(),
}

#: States in which no plaintext may be returned to any caller as though it
#: were whole. ``TRUNCATED`` is in here deliberately: its partial bytes are
#: served through the redaction policy, never as plain captured content.
NON_READABLE_CONTENT_STATES: frozenset[ContentState] = frozenset(
    {
        ContentState.REDACTED,
        ContentState.TRUNCATED,
        ContentState.EXPIRED,
        ContentState.UNAVAILABLE,
    }
)


def may_transition(source: ContentState, target: ContentState) -> bool:
    """Return True when ``source`` may legally become ``target``."""
    return target in CONTENT_STATE_TRANSITIONS[source]


class ContentDescriptor(BaseModel):
    """What a read endpoint may say about one piece of call content.

    The load-bearing clause is on ``UNAVAILABLE``: a byte count or an expiry
    timestamp would turn the descriptor into an existence oracle, letting an
    unauthorized caller distinguish "purged" from "not yours" and so confirm
    that a session id is real.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ContentState
    byte_count: int | None = Field(default=None, ge=0)
    expires_at: float | None = None

    @model_validator(mode="after")
    def _unavailable_is_not_an_oracle(self) -> ContentDescriptor:
        """``UNAVAILABLE`` must carry no metadata that implies existence."""
        if self.state is ContentState.UNAVAILABLE and (
            self.byte_count is not None or self.expires_at is not None
        ):
            raise ValueError(
                "UNAVAILABLE content must not report byte_count or "
                "expires_at: doing so is an existence oracle"
            )
        return self


class EnvelopeHeader(BaseModel):
    """Cleartext header travelling beside a sealed value.

    ``crypto.py`` today stores a bare MultiFernet token with none of this, so
    a stored secret cannot say which key sealed it or what it was bound to.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Which key sealed the value. Required for targeted rotation.
    key_id: str = Field(min_length=1)
    #: Bumped when the envelope format itself changes.
    version: int = Field(ge=1)
    #: Opaque algorithm label. Wave 0 picks no backend.
    algorithm: str = Field(min_length=1)
    #: Additional authenticated data, so a ciphertext cannot be lifted from
    #: one row and pasted into another.
    aad: str = Field(min_length=1)


class SealedValue(BaseModel):
    """A sealed secret plus the header needed to unseal and rotate it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    header: EnvelopeHeader
    ciphertext: str = Field(min_length=1)


@runtime_checkable
class EnvelopeCodec(Protocol):
    """The interface Wave 3 must implement. No implementation ships here."""

    def seal(self, plaintext: str, *, aad: str) -> SealedValue:
        """Seal ``plaintext``, binding it to ``aad``."""
        ...

    def unseal(self, sealed: SealedValue, *, aad: str) -> str:
        """Return the plaintext, refusing a mismatched ``aad``."""
        ...

    def rewrap(self, sealed: SealedValue, *, key_id: str) -> SealedValue:
        """Re-seal under ``key_id`` without exposing plaintext to the caller."""
        ...


class SensitiveAccessEvent(BaseModel):
    """One audited read of sensitive content.

    ``extra="forbid"`` is the point: the event structurally cannot carry the
    content it describes, so turning on audit logging can never itself become
    the leak. The existing ``config_audit_log`` has no tenant, no actor, and
    records no reads at all.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    occurred_at: float
    actor_kind: PrincipalKind
    #: The ``vk_`` key id when one authenticated, else ``None``.
    actor_key_id: int | None = None
    #: The tenant the actor was bound to. ``None`` for an operator.
    tenant_id: str | None = None
    #: The tenant that owns the row being read, which is what makes a
    #: cross-tenant access visible in the log.
    resource_tenant_id: str
    resource_kind: Literal["transcript", "replay", "session", "tool_call"]
    resource_id: str
    action: Literal["read", "export", "purge", "redact"]
    decision: Literal["allow", "deny"]
    #: Short machine-readable reason, never free-form content.
    reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _tenant_key_audit_identity_is_complete(self) -> SensitiveAccessEvent:
        """Keep every sensitive read attributable to both actor and resource."""
        if self.actor_kind is PrincipalKind.TENANT_KEY and self.tenant_id is None:
            raise ValueError("tenant-key audit events require tenant_id")
        return self


#: Field names Codex's trace contract is expected to expose, held as strings
#: so this package never imports ``voicegateway.telemetry``. The binding test
#: skips while that module is absent and becomes a real gate the moment it
#: lands.
TELEMETRY_FIELD_BINDINGS: Mapping[str, str] = {
    "tenant_id": "voicegateway.telemetry.trace_schema.SpanAttributes.tenant_id",
    "session_id": "voicegateway.telemetry.trace_schema.SpanAttributes.session_id",
    "trace_id": "voicegateway.telemetry.trace_schema.SpanContext.trace_id",
    "span_id": "voicegateway.telemetry.trace_schema.SpanContext.span_id",
    "turn_index": "voicegateway.telemetry.trace_schema.SpanAttributes.turn_index",
}

#: The module this package must never import. Asserted by an AST guard.
FORBIDDEN_IMPORT_ROOT = "voicegateway.telemetry"


def _load_json(filename: str) -> object:
    """Read one packaged JSON data file as UTF-8."""
    raw = resources.files(_DATA_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    return json.loads(raw)


def load_authorization_matrix() -> AuthorizationMatrix:
    """Load and validate ``authorization_matrix.json``."""
    return AuthorizationMatrix.model_validate(_load_json("authorization_matrix.json"))


def load_threat_model() -> ThreatModel:
    """Load and validate ``threat_model.json``."""
    return ThreatModel.model_validate(_load_json("threat_model.json"))


__all__ = [
    "AuthorizationMatrix",
    "AuthorizationRule",
    "CONTENT_STATE_TRANSITIONS",
    "ContentDescriptor",
    "ContentState",
    "ContractStatus",
    "EXISTING_SCOPES",
    "EnvelopeCodec",
    "EnvelopeHeader",
    "FORBIDDEN_IMPORT_ROOT",
    "GAP_ID_PATTERN",
    "GapId",
    "NON_READABLE_CONTENT_STATES",
    "PLANNED_SCOPES",
    "PlannedRoute",
    "PrincipalKind",
    "RouteAuth",
    "ScopeName",
    "SealedValue",
    "SensitiveAccessEvent",
    "TELEMETRY_FIELD_BINDINGS",
    "ThreatEntry",
    "ThreatId",
    "ThreatModel",
    "load_authorization_matrix",
    "load_threat_model",
    "may_transition",
]
