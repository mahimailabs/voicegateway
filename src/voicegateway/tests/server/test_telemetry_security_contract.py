"""Wave 0: the security contract's own shape, plus its absence guards.

Nothing here touches a running request path. The tests assert three kinds of
thing:

- the contract vocabulary behaves as declared (enums, validators, the
  content-state invariants, the envelope Protocol),
- the *absence* guards hold, so a "planned" claim is falsifiable: the two
  planned scopes genuinely do not exist in production source yet,
- the zero-coupling rules hold, so this package can be written before Codex's
  ``voicegateway.telemetry`` module lands and still gate it once it does.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from voicegateway.schemas.telemetry.security_schema import (
    CONTENT_STATE_TRANSITIONS,
    EXISTING_SCOPES,
    FORBIDDEN_IMPORT_ROOT,
    NON_READABLE_CONTENT_STATES,
    PLANNED_SCOPES,
    TELEMETRY_FIELD_BINDINGS,
    AuthorizationRule,
    ContentDescriptor,
    ContentState,
    ContractStatus,
    EnvelopeCodec,
    EnvelopeHeader,
    PrincipalKind,
    RouteAuth,
    ScopeName,
    SealedValue,
    SensitiveAccessEvent,
    ThreatEntry,
    ThreatModel,
    load_threat_model,
    may_transition,
)

#: Repository root, four parents up from tests/server/.
_SRC = Path(__file__).resolve().parents[2]
_SCHEMA_PACKAGE = _SRC / "schemas" / "telemetry"
_SERVER = _SRC / "server"


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


def test_existing_and_planned_scopes_are_disjoint():
    """A scope cannot be both already-enforced and still-to-build."""
    assert not (EXISTING_SCOPES & PLANNED_SCOPES)
    assert EXISTING_SCOPES | PLANNED_SCOPES == set(ScopeName)


def test_principal_kinds_cover_every_auth_branch():
    """Every branch _deps.py can resolve has a name in the contract."""
    assert {
        PrincipalKind.OPERATOR,
        PrincipalKind.ADMIN_KEY,
        PrincipalKind.TENANT_KEY,
        PrincipalKind.STATIC_KEY,
        PrincipalKind.MCP_TOKEN,
    } == set(PrincipalKind)


# --------------------------------------------------------------------------
# AuthorizationRule validators
# --------------------------------------------------------------------------


def _rule(**overrides) -> dict:
    base = {
        "method": "GET",
        "path": "/v1/costs",
        "auth": RouteAuth.OPEN,
        "status": ContractStatus.GAP,
        "tenant_scoped": True,
        "gap_id": "VG-SEC-004",
        "wave": 1,
    }
    base.update(overrides)
    return base


def test_gap_row_requires_a_gap_id_and_a_wave():
    """A gap with no owner is prose, so the model refuses it."""
    with pytest.raises(ValidationError, match="requires both gap_id and wave"):
        AuthorizationRule.model_validate(_rule(gap_id=None))
    with pytest.raises(ValidationError, match="requires both gap_id and wave"):
        AuthorizationRule.model_validate(_rule(wave=None))


def test_enforced_row_must_not_claim_a_gap():
    """An enforced row carrying a gap id would corrupt the gap-id join."""
    with pytest.raises(ValidationError, match="carries no gap_id"):
        AuthorizationRule.model_validate(
            _rule(status=ContractStatus.ENFORCED, wave=None)
        )


def test_matrix_row_cannot_be_planned():
    """PLANNED belongs to planned_routes; a row exists iff the route does."""
    with pytest.raises(ValidationError, match="use planned_routes"):
        AuthorizationRule.model_validate(_rule(status=ContractStatus.PLANNED))


def test_matrix_row_cannot_be_closed():
    """CLOSED is threat-model history. A closed route's row is ENFORCED.

    Without this, a CLOSED row would fall through to the gap branch, be
    required to carry a gap_id, and then be counted as open work by
    everything that reads the matrix.
    """
    with pytest.raises(ValidationError, match="belongs to the threat"):
        AuthorizationRule.model_validate(_rule(status=ContractStatus.CLOSED))


def test_gap_id_format_is_enforced():
    """Free-form ids would break the verbatim join across the four places."""
    with pytest.raises(ValidationError):
        AuthorizationRule.model_validate(_rule(gap_id="VG-SEC-4"))
    with pytest.raises(ValidationError):
        AuthorizationRule.model_validate(_rule(gap_id="SEC-004"))


def test_rows_are_frozen_and_reject_unknown_fields():
    """The matrix is data, so a typo must fail loudly rather than be ignored."""
    with pytest.raises(ValidationError):
        AuthorizationRule.model_validate(_rule(tenant_scope=True))
    rule = AuthorizationRule.model_validate(_rule())
    with pytest.raises(ValidationError):
        rule.status = ContractStatus.ENFORCED


# --------------------------------------------------------------------------
# Closing a gap: the entry stays as history
# --------------------------------------------------------------------------


def _entry(**overrides) -> dict:
    base = {
        "gap_id": "VG-SEC-001",
        "threat": "VG-THREAT-001",
        "title": "t",
        "description": "d",
        "status": ContractStatus.GAP,
        "wave": 1,
        "evidence": "src/voicegateway/repository/tool_calls_repository.py:54",
    }
    base.update(overrides)
    return base


def test_closed_entry_requires_release_and_commit():
    """A closure with no release or commit is a claim with no receipt."""
    with pytest.raises(ValidationError, match="closed_in and closed_by"):
        ThreatEntry.model_validate(_entry(status=ContractStatus.CLOSED))
    entry = ThreatEntry.model_validate(
        _entry(status=ContractStatus.CLOSED, closed_in="0.26.0", closed_by="abc1234")
    )
    assert entry.closed_in == "0.26.0"


def test_open_entry_cannot_carry_closure_fields():
    """Closure fields on an open gap would read as closed to a skimmer."""
    with pytest.raises(ValidationError, match="only a closed entry"):
        ThreatEntry.model_validate(_entry(closed_in="0.26.0"))


def test_threat_model_open_gap_ids_excludes_closed():
    """gap_ids stays complete for the contiguity pin; open_gap_ids is the work."""
    closed = _entry(
        status=ContractStatus.CLOSED, closed_in="0.26.0", closed_by="abc1234"
    )
    open_entry = _entry(gap_id="VG-SEC-002", status=ContractStatus.PLANNED)
    model = ThreatModel.model_validate([closed, open_entry])
    assert model.gap_ids() == {"VG-SEC-001", "VG-SEC-002"}
    assert model.open_gap_ids() == {"VG-SEC-002"}


# --------------------------------------------------------------------------
# Content state
# --------------------------------------------------------------------------


def test_content_state_vocabulary_matches_the_roadmap():
    """Waves 3 and 4 enumerate these five verbatim."""
    assert [s.value for s in ContentState] == [
        "captured",
        "redacted",
        "truncated",
        "expired",
        "unavailable",
    ]


def test_content_transitions_are_one_way():
    """No state may reach a state that precedes it in the lifecycle."""
    order = [
        ContentState.CAPTURED,
        ContentState.TRUNCATED,
        ContentState.REDACTED,
        ContentState.EXPIRED,
        ContentState.UNAVAILABLE,
    ]
    for index, source in enumerate(order):
        for target in CONTENT_STATE_TRANSITIONS[source]:
            assert order.index(target) > index, f"{source} -> {target} moves backward"


def test_only_captured_is_readable():
    """Every state but CAPTURED withholds plaintext from every caller."""
    assert NON_READABLE_CONTENT_STATES == frozenset(ContentState) - {
        ContentState.CAPTURED
    }


def test_every_state_is_in_the_transition_map():
    """A new state added to the enum must declare its transitions."""
    assert set(CONTENT_STATE_TRANSITIONS) == set(ContentState)


def test_unavailable_is_terminal():
    """The unauthorized projection cannot become anything else."""
    assert CONTENT_STATE_TRANSITIONS[ContentState.UNAVAILABLE] == frozenset()
    assert not may_transition(ContentState.UNAVAILABLE, ContentState.CAPTURED)


def test_expired_content_never_returns():
    """A late ingest replaying an older revision must not resurrect content."""
    assert not may_transition(ContentState.EXPIRED, ContentState.CAPTURED)
    assert not may_transition(ContentState.REDACTED, ContentState.CAPTURED)
    assert not may_transition(ContentState.TRUNCATED, ContentState.CAPTURED)


@pytest.mark.parametrize("field", ["byte_count", "expires_at"])
def test_unavailable_content_is_not_an_existence_oracle(field):
    """Byte counts and expiry on UNAVAILABLE would confirm the id is real."""
    with pytest.raises(ValidationError, match="existence oracle"):
        ContentDescriptor.model_validate({"state": ContentState.UNAVAILABLE, field: 1})


def test_unavailable_content_with_no_metadata_is_valid():
    """The only legal UNAVAILABLE descriptor says nothing at all."""
    descriptor = ContentDescriptor.model_validate({"state": ContentState.UNAVAILABLE})
    assert descriptor.byte_count is None
    assert descriptor.expires_at is None


def test_expired_may_report_zero_bytes():
    """The oracle rule is narrow: an authorized caller still gets the truth."""
    descriptor = ContentDescriptor.model_validate(
        {"state": ContentState.EXPIRED, "byte_count": 0}
    )
    assert descriptor.byte_count == 0


# --------------------------------------------------------------------------
# Envelope interface
# --------------------------------------------------------------------------


class _StubCodec:
    """Minimal shape check. Deliberately not a working implementation."""

    def seal(self, plaintext: str, *, aad: str) -> SealedValue:
        return SealedValue(
            header=EnvelopeHeader(key_id="k1", version=1, algorithm="stub", aad=aad),
            ciphertext=plaintext[::-1],
        )

    def unseal(self, sealed: SealedValue, *, aad: str) -> str:
        if sealed.header.aad != aad:
            raise ValueError("aad mismatch")
        return sealed.ciphertext[::-1]

    def rewrap(self, sealed: SealedValue, *, key_id: str) -> SealedValue:
        return SealedValue(
            header=sealed.header.model_copy(update={"key_id": key_id}),
            ciphertext=sealed.ciphertext,
        )


def test_envelope_codec_protocol_is_satisfiable():
    """The Protocol is implementable without picking a backend."""
    assert isinstance(_StubCodec(), EnvelopeCodec)


def test_envelope_header_requires_key_id_version_and_aad():
    """These three are exactly what crypto.py's bare token lacks today."""
    for missing in ("key_id", "version", "algorithm", "aad"):
        payload = {"key_id": "k1", "version": 1, "algorithm": "a", "aad": "x"}
        del payload[missing]
        with pytest.raises(ValidationError):
            EnvelopeHeader.model_validate(payload)


def test_envelope_header_rejects_empty_key_id_and_aad():
    """An empty aad would silently disable the binding it exists to provide."""
    with pytest.raises(ValidationError):
        EnvelopeHeader(key_id="", version=1, algorithm="a", aad="x")
    with pytest.raises(ValidationError):
        EnvelopeHeader(key_id="k", version=1, algorithm="a", aad="")


# --------------------------------------------------------------------------
# Sensitive access audit record
# --------------------------------------------------------------------------


def _event(**overrides) -> dict:
    base = {
        "occurred_at": 1.0,
        "actor_kind": PrincipalKind.TENANT_KEY,
        "tenant_id": "acme",
        "resource_tenant_id": "acme",
        "resource_kind": "transcript",
        "resource_id": "s-1",
        "action": "read",
        "decision": "allow",
    }
    base.update(overrides)
    return base


def test_audit_event_cannot_carry_content():
    """extra=forbid is the guarantee: enabling audit cannot become the leak."""
    with pytest.raises(ValidationError):
        SensitiveAccessEvent.model_validate(_event(text="hello, my card is ..."))
    with pytest.raises(ValidationError):
        SensitiveAccessEvent.model_validate(_event(payload={"transcript": "..."}))


def test_audit_event_records_both_tenants():
    """Actor tenant and resource tenant differ exactly when access is cross-tenant."""
    event = SensitiveAccessEvent.model_validate(
        _event(tenant_id="acme", resource_tenant_id="beta", decision="deny")
    )
    assert event.tenant_id != event.resource_tenant_id


def test_audit_event_reason_is_bounded():
    """A reason field with no cap is a content field wearing a hat."""
    with pytest.raises(ValidationError):
        SensitiveAccessEvent.model_validate(_event(reason="x" * 201))


def test_audit_event_requires_complete_tenant_identity():
    """A tenant-key read without both tenants cannot be investigated later."""
    with pytest.raises(ValidationError, match="tenant-key audit events require"):
        SensitiveAccessEvent.model_validate(_event(tenant_id=None))
    with pytest.raises(ValidationError):
        SensitiveAccessEvent.model_validate(_event(resource_tenant_id=None))


def test_threat_model_rejects_duplicate_gap_ids():
    """Duplicate ids would make by_gap_id silently discard one finding."""
    entry = load_threat_model().root[0]
    with pytest.raises(ValidationError, match="duplicate threat-model gap ids"):
        ThreatModel.model_validate([entry, entry])


# --------------------------------------------------------------------------
# Absence guards: the "planned" claims must be falsifiable
# --------------------------------------------------------------------------


def _server_sources() -> list[Path]:
    return sorted(_SERVER.rglob("*.py"))


def test_planned_scopes_are_absent_from_production():
    """If Wave 1 has shipped these, this test must be deleted with the gap."""
    scope_literals = set()
    for path in _server_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "require_scope"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                scope_literals.add(node.args[0].value)
    for planned in PLANNED_SCOPES:
        assert planned.value not in scope_literals, (
            f"scope {planned.value!r} now exists in production; close the gap "
            "and delete this guard"
        )


def test_existing_scopes_really_are_used():
    """The other half of the claim: these scopes are live, not aspirational.

    ``ingest`` joined the list in 0.26.0 and is spelled differently: it is
    enforced by a named dependency rather than a ``require_scope`` literal,
    because the route needs the resulting Principal and not just a pass/fail.
    Asserting the dependency's own name keeps the claim honest without
    pretending the two gates have the same shape.
    """
    source = "\n".join(p.read_text(encoding="utf-8") for p in _server_sources())
    assert 'require_scope("write")' in source
    assert "require_scope(ADMIN_SCOPE)" in source
    assert "require_ingest_principal" in source


def test_require_scope_closure_shape_is_stable():
    """The matrix generator recovers the scope from this closure.

    Assert the shape it depends on directly, so a refactor of ``_deps.py``
    fails here with an actionable message rather than silently mislabelling
    every row in the matrix.
    """
    from voicegateway.server.api._deps import require_scope

    dep = require_scope("write")
    assert dep.__qualname__ == "require_scope.<locals>._dep"
    assert dep.__code__.co_freevars == ("scope",), (
        "require_scope's closure changed shape; update classify() in "
        "tools/scripts/gen_authorization_matrix.py to match"
    )
    cell = dep.__closure__[dep.__code__.co_freevars.index("scope")]
    assert cell.cell_contents == "write"


# --------------------------------------------------------------------------
# Zero coupling to the Codex-owned trace contract
# --------------------------------------------------------------------------


def test_schema_package_never_imports_telemetry_module():
    """'Zero coupling' is executable, not a promise in a comment."""
    offenders = []
    for path in sorted(_SCHEMA_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == FORBIDDEN_IMPORT_ROOT or name.startswith(
                    FORBIDDEN_IMPORT_ROOT + "."
                ):
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, offenders


def test_field_bindings_are_dotted_paths_under_the_telemetry_root():
    """Bindings are strings on purpose, so they cannot become imports."""
    assert TELEMETRY_FIELD_BINDINGS
    for field, target in TELEMETRY_FIELD_BINDINGS.items():
        assert target.startswith(FORBIDDEN_IMPORT_ROOT + "."), target
        assert target.rsplit(".", 1)[-1] == field, (
            f"binding for {field!r} must end in that field name, got {target!r}"
        )


def test_field_bindings_resolve_once_codex_ships():
    """Skips while voicegateway.telemetry is absent; a real gate afterwards."""
    if importlib.util.find_spec(FORBIDDEN_IMPORT_ROOT) is None:
        pytest.skip(f"{FORBIDDEN_IMPORT_ROOT} does not exist yet (Wave 0)")

    import importlib as _importlib

    unresolved = []
    for field, target in TELEMETRY_FIELD_BINDINGS.items():
        module_path, _, attr = target.rpartition(".")
        class_path, _, class_name = module_path.rpartition(".")
        try:
            module = _importlib.import_module(class_path)
            owner = getattr(module, class_name)
        except (ImportError, AttributeError):
            unresolved.append(f"{field}: cannot resolve {module_path}")
            continue
        fields = getattr(owner, "model_fields", None)
        names = set(fields) if fields else set(vars(owner))
        if attr not in names:
            unresolved.append(f"{field}: {class_name} has no {attr!r}")
    assert not unresolved, unresolved
