---
title: Observability contracts
description: "Wave 0 trace vocabulary, W3C propagation rules, correlation fields and compatibility boundaries"
---

Wave 0 defines the trace vocabulary before any OTLP receiver, database table,
or instrumented provider exists. The implementation lives in
`voicegateway.telemetry` and is intentionally internal: importing it does not
start an exporter, alter a voice session, or pull in an OpenTelemetry SDK.

## Scope

The contract models a trace as spans, events and links. It is transport- and
storage-independent so the same record can later be built by native wrappers,
OTLP ingestion, or a replay importer.

| Type | Purpose |
| :--- | :--- |
| `SpanContext` | W3C trace identity, flags, tracestate and remote-parent marker. |
| `SpanAttributes` | Explicit internal correlation: tenant, project, session, turn, agent revision and workflow version. |
| `InstrumentationScope` | The library/component that created a span. |
| `SpanEvent` | Timestamped facts within a span, such as retry or late replay receipt. |
| `SpanLink` | Asynchronous causal relationship without inventing a second parent. |
| `SpanRecord` | The complete immutable span: timing, status, resource, scope, attributes, events, links and correlation. |

Every model rejects unknown fields. Attribute keys are non-empty and unique
within their collection. Attribute values support OpenTelemetry `AnyValue`:
null, strings, booleans, signed 64-bit integers, finite doubles, bytes, arrays
and nested maps. Non-OTLP JSON renders bytes as base64, following the
OpenTelemetry non-OTLP representation; the future OTLP receiver will preserve
native wire types.

## W3C propagation

`parse_traceparent()` strictly validates incoming lower-case trace IDs and span
IDs. Version `00` accepts exactly the four W3C fields. Higher versions retain
the required core fields and ignore their extension fields. `format_traceparent()`
emits version `00` when VoiceGateway creates a child span, carrying only the
sampled bit permitted by that version.

`parse_tracestate()` validates ordering, key uniqueness and the 32-member
limit, then returns the accepted header unchanged. A transport adapter decides
whether an invalid incoming header starts a fresh trace; the low-level parser
raises instead of silently changing correlation.

Task-local propagation uses a `ContextVar`. Callers must retain the token from
`set_trace_context()` and pass it to `reset_trace_context()` so nested work
restores the exact prior context.

## Correlation and privacy

`SpanAttributes.tenant_id` is internal-authoritative. It is set from a verified
principal at an ingestion boundary; an arbitrary external attribute with the
same name never overrides it. This contract does not carry prompt text,
transcripts, tool arguments, tool results, or encrypted payload bytes. Those
belong to the security content contract and a later encrypted content plane.

| Existing field | Wave 0 mapping | Notes |
| :--- | :--- | :--- |
| `RequestRecord.session_id` | `SpanAttributes.session_id` | Existing best-effort session correlation. |
| `RequestRecord.turn_index` | `SpanAttributes.turn_index` | Nullable when turn tracking is unavailable. |
| `RequestRecord.revision` | `SpanAttributes.agent_revision` | Opaque revision identifier. |
| `Request.tenant_id` | `SpanAttributes.tenant_id` | Must be server-derived at ingest. |
| `Session.project` / request project | `SpanAttributes.project_id` | Explicit project dimension. |
| `ToolCall.call_id` | Span attribute in a later tool adapter | No tool payload is captured in Wave 0. |

## Topology rules

- A caller turn or logical LLM/tool operation is one parent span.
- Each retry or provider fallback is a child attempt span sharing its parent
  trace ID; the attempt does not become an unrelated trace.
- Provider acknowledgement and verification are distinct spans or events so an
  acknowledged write is never confused with a verified external effect.
- Research, workflow, queue, and other background work use a span link when it
  is causal but not strictly nested work.
- Timestamps are Unix epoch nanoseconds and an end time may not precede its
  start time.

## Versioning and deferred work

`SpanRecord.schema_version` starts at `1`. Stored records will retain that
version so future readers can select a compatible decoder. Wave 0 intentionally
does not add migrations, persistence, OTLP endpoints, SDK dependencies, GenAI
semantic-convention constants, replay storage, UI views, or public root-package
exports. Those land in later waves after their corresponding transport,
security and retention decisions are implemented.
