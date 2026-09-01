# Cross-tenant security fixtures (Wave 0)

Five cases, each one request by one actor against one victim tenant, plus what
the contract says must happen. They are data, not tests: the runner lives in
`tests/server/test_telemetry_tenant_isolation_contract.py`.

## Why this directory is not `fixtures/telemetry/`

The roadmap named `fixtures/telemetry/` for this work, but Codex's trace
contract claims that path in the same wave. Two agents writing different
schemas into one directory in one wave is a collision with no upside, so the
security cases live here instead. Codex has been notified; see the Codex
handoff section of `docs/architecture/observability-security.md`.

There is no `__init__.py`, matching `fixtures/streaming/`. The modules import
fine as a namespace package.

## Provenance

Every `observed` block records a measurement taken against an unmodified
checkout at `a03afa5`, not an expectation. The three kinds differ in what they
prove:

| Case | Kind | Gap | What it proves |
| :--- | :--- | :--- | :--- |
| `ingest_tool_calls_tenant_override` | characterization | VG-SEC-001 | A live cross-tenant **write**. An acme key produced one row tagged beta and zero tagged acme. |
| `read_tenant_param_override` | guarantee | none | The read path already refuses a foreign `tenant` param with 403. |
| `session_detail_foreign_id` | guarantee | none | A foreign session id returns 404, not 403, so it is not an existence oracle. |
| `audit_log_open_read` | characterization | VG-SEC-004 | The audit log answers a caller with no credential. |
| `project_param_unscoped` | absence | VG-SEC-002 | No surface exists to authorize a project, so there is nothing to characterize. |

Two of the five record guarantees rather than defects. That is deliberate. A
format that can only express what is broken cannot tell you when something
that worked stops working, and the read-side rules these two cover are the
exact rules the write side gets wrong.

## Kinds and their runner shapes

- **`guarantee`** — asserted as an ordinary passing test. Carries no `gap_id`
  and no `observed` block; the validator rejects both.
- **`characterization`** — asserted twice. Once against `observed`, labelled
  as documenting today's wrong behavior, and once against `contract` under
  `xfail(strict=True)`. The strict marker is what stops the contract assertion
  from being quietly deleted when the fix lands: it XPASSes, strict turns that
  into a failure, and whoever fixed it has to remove the marker deliberately.
  The validator rejects a characterization whose `observed` equals its
  `contract`.
- **`absence`** — asserts the named attributes genuinely do not exist yet.

## Adding a case

Write the JSON, run the suite. `_schema.py` will tell you what a case of that
kind is missing. Keep `observed` a measurement: run the request first, paste
what actually came back, and say so in the note.
