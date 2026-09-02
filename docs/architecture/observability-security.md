---
title: Observability security contracts
description: "Wave 0: the threat model, authorization matrix, isolation rules, content-state and envelope interfaces that the telemetry work must satisfy"
---

Wave 0 freezes the security contracts for the observability work before any
ingestion or storage for it exists. Nothing on this page changes runtime
behavior. The output is a threat model, an authorization matrix bound to the
live route table, a set of cross-tenant fixtures, and tests that assert the
contract's own shape.

The point of doing this first is that authentication, tenancy, redaction,
encryption and audit get decided before there are endpoints to retrofit them
onto.

## How a gap stays honest

Every finding gets a gap id, minted once in `threat_model.json`. The same id
then appears verbatim in the authorization matrix, in a fixture, in a test,
and on this page. A test asserts those references agree, so a gap cannot be
renamed in one place and left stale in another, and it cannot be quietly
dropped.

Three statuses drive three different test shapes:

| Status | Meaning | Test shape |
| :--- | :--- | :--- |
| Enforced | Production already meets the contract | Conformance probe. Must pass. |
| Gap | The surface exists and production deviates | A characterization test asserting today's wrong behavior, plus a `xfail(strict=True)` test asserting the contract. |
| Planned | No surface exists yet | An absence guard asserting the field, scope or column genuinely is not there. |

The strict xfail is the mechanism that makes Wave 1 self-closing. When a fix
lands, the contract test XPASSes, `strict=True` turns that into a failure, and
whoever fixed it has to delete the marker and the matching characterization
together. A gap cannot be closed while leaving a test that still asserts the
broken behavior.

## Threats

Seven threats, fourteen gaps. Each gap carries evidence as a `file.py:line`
reference into production source, so every claim here can be checked by
opening one file at one line.

### VG-THREAT-001: cross-tenant write

**VG-SEC-001** (closed in 0.26.0) was live through Wave 0.
`POST /v1/ingest/tool-calls` admitted `tenant_id` through its field
allow-list, and the repository resolved the row tenant as the payload value
when one was present, falling back to the key-derived tenant only when it was
absent. A key scoped to one tenant could therefore write rows attributed to
another. Measured against an unmodified checkout, a batch posted with one
tenant's key produced one row tagged the other tenant and none tagged its own.

Closed by making tenancy explicit rather than ambient. All seven writers now
take `tenant_id` as a required keyword, `create_tool_calls` no longer reads
the field off the row, and no module under `repository/` can import
`current_tenant`. The fixture that characterized the defect is kept as a
guarantee: it is the exact request that used to succeed, so it is the one
that must keep failing to write.

The sibling writers do not share this shape, which is what makes it a defect
rather than a design. The turns and dead-air writers apply a single tenant,
resolved from the function argument, to every row. `log_request` reads the
request-scoped tenant and ignores the payload entirely. The comment above the
allow-list already argues that an agent-supplied primary key would let one
agent overwrite another's row. That is the same argument, and it should have
excluded `tenant_id` too.

This also contradicts a published guarantee. See
[security](/architecture/security), which states that the `tenant_id` field in
request bodies is advisory only and cannot override the key-derived tenant.
For this one route, it can.

**VG-SEC-015** (planned, Wave 1) is the same shape, one wave ahead of the code.
The trace contract in
[observability contracts](/architecture/observability-contracts) states that
`SpanAttributes.tenant_id` is internal-authoritative, set from a verified
principal at an ingestion boundary. No such boundary exists yet, and the
contract carries a single tenant slot with no way to distinguish what a payload
asserted from what the server decided. That is structurally the arrangement
which produced VG-SEC-001, and the lesson of VG-SEC-001 is that writing the
rule down is not enough: it was written down, in the security page above, while
the code did the opposite.

So the rule is recorded as data rather than prose. Each planned OTLP route
carries `tenant_source: server_derived`, and a validator on `PlannedRoute`
refuses any planned ingest route that does not. The receiver cannot be written
without a row stating where its tenant comes from.

### VG-THREAT-002: cross-tenant and unauthenticated read

**VG-SEC-004** (gap, Wave 1): 28 of the 89 routes resolve no auth dependency
at all and answer an unauthenticated caller regardless of configured keys. The
set includes the audit log, costs, logs, metrics and the billing rate-card
reads. `tests/server/test_auth.py::test_reads_open_regardless_of_auth` pins
this as intended for the current slice, so that pin and this gap have to be
closed in the same change.

**VG-SEC-005** (closed in 0.26.0) was the soft default: with no keys
configured, the read path resolved a full admin principal with no tenant
binding, so the difference between a locked deployment and an open one was
a config block rather than a code path. It now runs through `decide()`,
which refuses under `enforce`, serves with a `vg.auth.would_refuse` warning
under `warn`, and stays silent only under the explicit
`auth.local_development` flag. That flag is itself refused at startup in
the company of configured keys, a non-loopback bind, or enforce mode, and
the check runs in `build_app` as well as the serve CLI so the container
entry point cannot bypass it.

The read side is not uniformly weak, and the contract records what already
works. A tenant-scoped key asking for another tenant's rows by query param is
refused before the query runs. A session id belonging to another tenant
returns the same 404 as an id that does not exist, so the endpoint does not
confirm the id is real. Both are captured as fixtures precisely so they cannot
regress unnoticed.

### VG-THREAT-003: missing project authorization

**VG-SEC-002** (planned, Wave 1): project-level authorization does not exist
in any form. There is no tenant column on managed projects, no project column
on API keys, no project field on the principal, and no check on the project
query parameter. 22 of the 89 routes accept a project identifier and none of
them compares it against anything the caller is entitled to.

Because there is no surface to hold the check, this is recorded as an absence
guard rather than a characterization: the fixture asserts the two attributes a
project binding would need do not exist yet.

### VG-THREAT-004: coarse and inert scopes

**VG-SEC-003** (closed in 0.26.0): there was no ingest scope. Telemetry
ingest was gated by the same `write` scope that guards provider, model and
project mutation, across 18 routes in total, so an agent key that only needed
to post telemetry could rewrite gateway configuration.

The six ingest routes (`POST /v1/ingest`, `/v1/ingest/turns`,
`/v1/ingest/tool-calls`, `/v1/ingest/dead-air`, `/v1/agents/heartbeat`,
`/v1/calls/observations`) now sit behind `require_ingest_principal`, which
enforces the `ingest` scope and returns the `Principal` whose `tenant_id` the
handler stamps on every row it writes. `write` is left covering 12 routes, all
of them config mutation.

Two compatibility grants keep existing keys working and both stop under
`auth.enforcement: enforce`:

- a key holding `write` but not `ingest` is still admitted, and each such
  request logs a warning naming the key id;
- a wildcard (`*`) key still matches, as it always has.

Both are withdrawn in 0.27.0, and until VG-SEC-006 closes there is no way to
mint a key that would survive that: `POST /v1/api-keys` takes no `scopes`
field, so every key the product issues is a wildcard. The two gaps therefore
have to close in that order, and the grants above are what keeps 0.26.0
shippable in between.

**VG-SEC-006** (gap, Wave 1): every key the product mints defaults to the
wildcard scope, and the scope check short-circuits on the wildcard. Scope
enforcement runs and always passes.

**VG-SEC-007** (gap, Wave 2): admin is enforced two ways, through a role
column and through the scope list, so a key can satisfy one and not the other.

**VG-SEC-008** (planned, Wave 3): MCP authentication is a single shared static
token with no per-caller identity, no tenant binding, and no `mcp:read` scope.

### VG-THREAT-005: ingest resource exhaustion

**VG-SEC-009** (gap, Wave 2): the batch cap is compared against the record
count only after the whole body has been deserialized, and there is no
`Content-Encoding` handling anywhere in the server. The cap bounds stored
records, not peak memory, which is the precise shape a compressed-payload
amplification attack exploits.

### VG-THREAT-006: sensitive content exposure

**VG-SEC-010** (planned, Wave 2): transcript text is a plain string column and
no redaction exists. Voice transcripts routinely carry names, addresses, card
numbers and health details.

**VG-SEC-011** (planned, Wave 3): the encryption envelope carries no key id,
no version and no associated data. Key resolution and rotation are solid, but
a stored ciphertext cannot say which key sealed it, so targeted rotation is
impossible and a value can be lifted from one row into another.

### VG-THREAT-007: audit blindness

**VG-SEC-012** (planned, Wave 2): audit records carry no tenant and no actor.
The log can say a provider was deleted but not who deleted it.

**VG-SEC-013** (planned, Wave 3): no read is audited. Reading a transcript or
exporting a replay leaves no trace, so a credential compromise that only reads
is invisible afterwards.

**VG-SEC-014** (gap, Wave 2): the audit writer wraps its insert and commit in
a bare exception handler that logs a warning and returns. An audit write can
fail silently while the mutation it records succeeds, which means absence of a
record does not imply absence of the event.

## Authorization matrix

`authorization_matrix.json` carries one row per live route: 89 rows against 89
routes, 49 enforced and 40 gaps. Rows are generated mechanically and then
hand-classified where the classification carries meaning.

Three tests keep it bound to reality:

- **Bijection.** Every live route has exactly one row and every row names a
  live route. Adding an endpoint without classifying it fails CI.
- **Classification.** The gating recorded on each row is compared against what
  the resolved dependency graph actually says, so a row cannot claim a route
  is guarded when it is not.
- **Absence.** Planned routes must not be servable yet.

Planned routes are kept in a separate list rather than mixed into the rows.
Putting them in the same list would have broken the bijection, since a row
would then exist for a route that does not. The separate list also gets a
stronger test: the path must genuinely not resolve today.

To add a route, run the generator and paste what it prints:

```bash
.venv/bin/python tools/scripts/gen_authorization_matrix.py
```

## Content states

Stored call content moves through five states: captured, redacted, truncated,
expired and unavailable. These are the roadmap's frozen vocabulary, and Waves 3
and 4 label content with exactly these words. Transitions are one-way, so an
expired recording cannot be resurrected by a later ingest replaying an older
revision, and a truncation is recorded rather than silently served as complete.

`unavailable` is the projection an unauthorized caller sees, and it is the
load-bearing part of the contract. A descriptor in that state may not report a
byte count or an expiry. Either one would let a caller distinguish "expired"
from "not yours", which turns the endpoint into an existence oracle for
session ids. This is the same rule the 404-not-403 fixture enforces at the
request level, stated once in the type system so both surfaces inherit it.

The rule is deliberately narrow. An authorized caller reading expired content
still gets the truth, including a zero byte count.

## Encryption envelope

The interface names a key id, an envelope version, an algorithm label and
associated data, with `seal`, `unseal` and `rewrap` operations. Wave 0 picks
no backend and ships no implementation. The point is that the four header
fields are exactly what the current bare token lacks, so VG-SEC-011 has a
concrete target rather than an aspiration.

## Sensitive access audit

`SensitiveAccessEvent` records who read what, with both the actor's tenant and
the resource's tenant, so a cross-tenant access is visible as a mismatch
between two fields rather than something a reader has to infer.

The record forbids unknown fields. That is the guarantee, not a style choice:
the event structurally cannot carry the content it describes, so turning audit
logging on can never itself become the leak. The reason field is length-capped
for the same reason, since an uncapped free-text field is a content field
wearing a hat.

## Compatibility

No production Python file is modified in this wave, no migration is added, and
no endpoint changes behavior. Two negatives are asserted directly in the
verification run: an empty diff against the migrations directory, and an empty
diff against the core, server and repository packages.

The one new runtime dependency is `importlib.resources` loading two JSON files
from inside the package, which follows the existing precedent for shipping
packaged data. Both files ship in the wheel; the fixtures do not, since the
test tree is excluded from the wheel already.

## Codex handoff

Wave 0's trace contract is Codex-owned and this wave deliberately does not
import it. Three mechanisms enforce that: field names are held as strings
rather than imports, a binding test skips while the telemetry module is absent
and becomes a real gate once it lands, and an AST guard asserts no import of
that module exists anywhere in the schema package.

### Final file paths

| Path | What it is |
| :--- | :--- |
| `src/voicegateway/schemas/telemetry/security_schema.py` | All exported contract types |
| `src/voicegateway/schemas/telemetry/threat_model.json` | The 14 gaps, with evidence |
| `src/voicegateway/schemas/telemetry/authorization_matrix.json` | 89 route rows plus planned routes |
| `src/voicegateway/tests/fixtures/security/` | The five cross-tenant fixtures |
| `src/voicegateway/tests/server/_telemetry_harness.py` | Shared app harness and route introspection |
| `src/voicegateway/tests/server/test_telemetry_security_contract.py` | Contract shape and absence guards |
| `src/voicegateway/tests/server/test_telemetry_tenant_isolation_contract.py` | Fixture-driven cross-tenant runner |
| `src/voicegateway/tests/server/test_telemetry_authorization_matrix.py` | Bijection and classification |
| `src/voicegateway/tests/server/test_telemetry_gap_ids.py` | The cross-reference keystone |
| `tools/scripts/gen_authorization_matrix.py` | Row generator |

### Exported names

From `voicegateway.schemas.telemetry.security_schema`:

Enums and aliases: `ContractStatus`, `ScopeName`, `PrincipalKind`, `ThreatId`,
`RouteAuth`, `ContentState`, `GapId`, `GAP_ID_PATTERN`.

Models: `AuthorizationRule`, `AuthorizationMatrix`, `PlannedRoute`,
`ThreatEntry`, `ThreatModel`, `ContentDescriptor`, `EnvelopeHeader`,
`SealedValue`, `SensitiveAccessEvent`.

Protocol: `EnvelopeCodec`, with `seal`, `unseal` and `rewrap`.

Constants and helpers: `EXISTING_SCOPES`, `PLANNED_SCOPES`,
`CONTENT_STATE_TRANSITIONS`, `NON_READABLE_CONTENT_STATES`,
`TELEMETRY_FIELD_BINDINGS`, `FORBIDDEN_IMPORT_ROOT`, `may_transition`,
`load_authorization_matrix`, `load_threat_model`.

### Deviations from the roadmap's named paths

1. **`security_schema.py`, not `security.py`.** Every schema module in this
   repo carries the `_schema` suffix.
2. **`tests/fixtures/security/`, not `tests/fixtures/telemetry/`.** The
   roadmap named the same directory for both agents in the same wave. Two
   schemas in one directory is a collision with no upside.
3. **No `voicegateway.telemetry` import exists, by design.** The binding map
   is strings, and an AST guard enforces the rule. The trace contract has now
   landed, so the binding test no longer skips: it resolves `tenant_id`,
   `session_id` and `turn_index` on `SpanAttributes` and `trace_id`, `span_id`
   on `SpanContext`, and fails if any of them moves. The zero-coupling
   mechanism worked as intended, and the gate is live.
4. **Planned routes are namespaced under `/v1/telemetry/`.** The conventional
   OTLP receiver mounts are `/v1/traces`, `/v1/metrics` and `/v1/logs`. Two of
   those already exist here as GET reads, so a bare OTLP mount would collide
   on path and differ only by method. This needs a decision from whoever owns
   the receiver.
5. **`PLANNED` never appears on a matrix row.** It would have broken the
   bijection. Planned routes are a separate list with an absence guard.
