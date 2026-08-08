---
title: "Phone calls"
description: "How VoiceGateway tells a phone call from a web session, what a SIP leg records, and how precise the answer-latency number is depending on which clock reported it."
---

Nothing else in these docs explains how VoiceGateway knows a call arrived over the phone rather than the web. This page does: channel detection, what a SIP leg records, and the three precisions behind the answer-latency number.

## Channel detection

[`attach()`](/guide/attach) classifies each session as `"telephony"` or `"web"` and stores it as `channel` on every captured row. Detection is framework-specific and best-effort: it never raises, and an inconclusive case returns no channel rather than a wrong guess.

**LiveKit.** Any remote participant with kind `PARTICIPANT_KIND_SIP` means `"telephony"`. A remote participant present and none of them SIP means `"web"`. No participant yet (or no job context) returns nothing.

**Pipecat.** VoiceGateway walks every processor in the pipeline, nested pipelines included, for a transport or serializer whose module name contains one of six tokens: `twilio`, `telnyx`, `plivo`, `exotel`, `genesys`, `vonage`. Any match means `"telephony"`. A transport matching none of them (Daily, WebRTC, a raw websocket) means `"web"`. No transport found returns nothing.

`attach()` takes a `channel=` keyword to skip detection and set the value directly. That works on Pipecat. On LiveKit, `attach()` does not currently forward `channel=` to the detection path, so the LiveKit channel is always the SIP-participant guess.

## What a SIP leg records

Two writers feed `calls` and `call_legs` for a phone call.

The **LiveKit webhook** (`POST /v1/livekit/webhook`) is the baseline. On `participant_joined` it stores the leg's kind, region, and a filtered copy of its attributes: every key prefixed `sip.` survives and everything else is dropped. In practice that is `sip.callID`, `sip.callStatus`, `sip.phoneNumber`, `sip.trunkID`, and `sip.ruleID`, but the filter is the prefix, not a fixed list, so any new `sip.*` attribute LiveKit adds is stored without a code change. A leg with no `sip.*` attributes stores nothing rather than an empty object, which keeps "we looked and found none" distinct from "nothing was reported".

`disconnect_reason` comes from LiveKit's `DisconnectReason` enum, read on `participant_left` and `participant_connection_aborted`. `SIP_TRUNK_FAILURE` marks a leg that never finished joining because the trunk itself failed: it arrives via `participant_connection_aborted`, the event for a leg that aborted mid-join rather than one that connected and later hung up.

`VOICEGW_LOADTEST_TRUNK_IDS` is a comma-separated list of SIP trunk ids treated as synthetic load. A joining leg whose `sip.trunkID` matches one stamps the call `is_probe=true`. Never `false`: a webhook that can't see the trunk id leaves the flag alone rather than clearing one another writer set. Probe traffic is never marked from a header or caller-supplied field, since anything on the wire can be forged.

**`POST /v1/calls/observations`** is the second writer: an agent or load worker's own self-report, for the part of the timeline only a process inside the call can see. A posted leg with `kind: "SIP"` is the caller leg as that reporter saw it, on its own clock. The endpoint is fire-and-forget (202, a background flusher writes it) and rejects, rather than silently drops, anything with no column: per-call SIP response codes, RTP loss/jitter/MOS, and a reported call duration are all refused. That refusal is a 422 on the field itself, and is distinct from a 429 (the queue is full, the report is dropped) or a 503 (observations are disabled, or this collector has no call storage).

## Answer latency precision

`answer_latency_ms` is `agent first_audio_track_at_ms` minus `caller joined_at_ms`: `livekit-sip` withholds the SIP `200 OK` until it subscribes to an audio track, so the agent's first published audio gates the caller's ring time. `answer_latency_source` records which clock produced those two timestamps, ranked strongest first:

| Source | Clock | Precision |
|---|---|---|
| `sipp_rtd` | The load worker's own INVITE-to-200 measurement, reported directly rather than derived. Only a load worker can report it: it placed the INVITE and saw the response. | Millisecond |
| `agent_report` | Both timestamps came from a self-report (`origin: agent` or `loadgen`) on `/v1/calls/observations` | Millisecond |
| `webhook_proxy` | At least one timestamp came from the LiveKit webhook only | Whole seconds: the webhook's `created_at` is a seconds value |

`webhook_proxy` is the default: zero instrumentation still produces a number, truncated to the nearest second. Once a `sipp_rtd` value is stored, a later webhook cannot downgrade it. Do not assume that protection extends to every pair: check the source column rather than the number alone when precision matters.

## What is not available

There is no per-call SIP response code and no RTP loss, jitter, or MOS: `livekit-api` exposes no `ListSIPCallInfo`, and the self-report endpoint refuses those fields rather than storing an empty column a UI could mistake for a real zero. See [What you can profile](/guide/what-you-can-profile) for the full per-call waterfall and why the SIP and SFU rows render a reason instead of a number.
