---
title: "Trunks and dispatch"
description: "The read-only SIP topology surface: what a trunk or dispatch rule row carries, where it comes from, and why VoiceGateway never writes one."
---
VoiceGateway reads three SIP objects from your LiveKit deployment: inbound trunks, outbound
trunks, and dispatch rules. It does not create, edit, or delete any of them. Routing changes
happen in LiveKit, not here.

## Read-only, not a management surface

`LiveKitAdmin` wraps `livekit.api` and calls three `SIPService` list methods:
`list_sip_inbound_trunk`, `list_sip_outbound_trunk`, `list_sip_dispatch_rule`. There is no
corresponding create, update, or delete call anywhere in VoiceGateway. LiveKit is the source
of truth for trunk and dispatch configuration; VoiceGateway only lists what's already there.
If you want a number ported, a trunk added, or a dispatch rule changed, do it against LiveKit
directly (the `lk` CLI or LiveKit's own console).

## What each row carries

**Inbound trunk** (`SipInboundTrunkRow`): `trunk_id`, `name`, `numbers` (the phone numbers
routed to this trunk).

**Outbound trunk** (`SipOutboundTrunkRow`): `trunk_id`, `name`, `address` (the SIP endpoint
you dial out to), `transport` (the SIP transport LiveKit reports, e.g. `SIP_TRANSPORT_TCP`),
`numbers`.

**Dispatch rule** (`SipDispatchRuleRow`): `rule_id`, `name`, `trunk_ids`. A rule with a
non-empty `trunk_ids` applies only to those trunks; an empty list means the rule applies to
any trunk.

Auth fields never reach these rows. LiveKit's SIP info messages carry `auth_username` and
`auth_password`; VoiceGateway strips them before the data leaves `LiveKitAdmin`, so a leaked
dashboard response can't leak trunk credentials.

## Credentials

The same three values every other control-plane read uses: `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET` (flags, then env, then a `livekit:` block in `voicegw.yaml`). There is no
separate SIP credential. The `livekit` extra (`voicegateway[livekit]`) must be installed for
any of this to import.

## When there's nothing to read

No LiveKit credentials resolved: the three list calls are never attempted. The section reports
`ok: false` with an explanatory error, and `inbound`, `outbound`, and `dispatch_rules` are all
empty.

Credentials resolved but the deployment runs no SIP service: the list calls themselves fail,
and the section reports `ok: false` with whatever error LiveKit's control plane returned.
Trunks and dispatch rules stay empty either way; a room or egress read failing independently
doesn't take this section down, and vice versa.

## What this doesn't give you

There's no per-call SIP detail here or anywhere else in VoiceGateway: `livekit-api` exposes no
`ListSIPCallInfo`, so nothing lets you join a specific call to the trunk or rule that routed
it. This page is topology only, not a call log.

For the dashboard screen that renders these three tables, see [The Server page](/guide/server-page).
For what a SIP call itself records once it lands, see [Phone calls](/guide/phone-calls). For
SIP capacity and answer-latency evidence under load, see [`voicegw loadtest`](/cli/loadtest).
