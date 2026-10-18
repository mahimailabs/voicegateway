# 0028: A hosted workspace is an organisation, created through an instance API

- Status: accepted
- Date: 2026-10-18

## Context

Decision 0024 has a customer go from sign-up to a paid workspace with no one from VoiceGateway acting, on the public image unchanged. That needs two answers this repository does not give yet: what a workspace is inside VoiceGateway, and how a program outside VoiceGateway creates one and lets its first admin in.

What already holds:

- Every project, member, key and read is scoped by organisation (decisions 0016 and 0027). Ids from another organisation are `not_found`, and a role gate runs on every route.
- 0017 puts "multiple organisations on one instance" and "organisation and instance management API" in the Enterprise column, and on in Cloud.
- 0027 has the control plane invite each workspace's first admin through the instance API, and the 06d invitation model leaves `invited_by` empty for it.
- 0027 also keeps a hosted identity service in the control plane for the billing account, so a Cloud customer signs in twice: once to pay, once to the product.

## Options

1. **One instance per workspace.** Each customer gets their own web, worker and databases. The strongest wall between customers, but every sign-up waits minutes for provisioning, every customer has their own URL and webhook address, and each stack is migrated, upgraded and backed up on its own.
2. **One shared instance, a workspace is an organisation.** The organisation scoping that exists today is the wall. An operator's program creates organisations and invites their first admin over HTTP. Sign-up takes seconds and there is one stack to run. The cost is that isolation rests on the access gate, so cross-organisation tests become a release gate.
3. **One shared instance, organisations created from the command line.** No new API, but the control plane would need shell access to the instance, which is harder to secure and to retry than a request.

## Decision

Option 2. A hosted workspace is one organisation on a shared instance. An instance API, under `ee/`, lets an operator's program create an organisation idempotently, invite its first admin and read its state, authenticated by one instance token.

## Consequences

- The instance API is Enterprise code (0017's row) in `ee/instance`. It is off unless `VOICEGATEWAY_INSTANCE_TOKEN` is set. Until license keys exist (0017), the `ee/` license, not a key check, governs its use; the check is added with keys.
- Its routes live under `/api/v1/instance` and go through the one access gate (0027) with their own scope, `instance`. The instance token is compared in constant time and never logged.
- An organisation's id is chosen by the caller, so a retried create never makes a second one.
- The first admin arrives by the 06d invitation, with no inviting user. When the instance has SMTP, it sends the email; otherwise the response carries the link for the caller to deliver.
- People sign in to a workspace with the product's own accounts only. The control plane needs no separate identity service: paying proves the email, and the payment provider's hosted pages handle billing changes. This replaces 0027's line that keeps one in the control plane, and removes the second sign-in it lists under "Gets harder".
- Cross-organisation isolation on every read, the live view, recordings, MCP and embeds stays the release gate 0024 named, now for one shared instance.
- A dedicated instance per customer stays possible later (Option 1) for an Enterprise plan; nothing here prevents it.
