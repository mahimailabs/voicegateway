# 0031: Recordings live in the instance's bucket, or a bucket the project brings at create

- Status: accepted
- Date: 2026-10-19
- Amends: 0008

## Context

0008 kept audio "only in the user's own bucket", written when VoiceGateway was self-hosted only, where the install's bucket is the user's own. Cloud (0024, 0028) is one shared instance, and its bucket (`vg-cloud-recordings` on Lightsail) belongs to VoiceGateway, not to the customer. Phase 07a stores recordings in whichever bucket the instance is configured with. Some customers will want their audio in their own S3 account; letting them move a project between buckets later would mean migrating stored audio, or recordings split across two buckets.

## Options

1. Every Cloud customer brings a bucket: audio never sits in VoiceGateway's storage, but no recording works until a customer sets up S3 and hands over keys.
2. The instance's bucket for everyone: zero setup, but a customer who needs their audio in their own account cannot have it.
3. The instance's bucket by default, and a project may bring its own S3 bucket when it is created, never changed afterwards.

## Decision

Option 3, decided by Mahimai on 2026-10-19: "by default lets store it in our bucket and give option to add their S3 (only before creating a project so that we dont worry about migrations)".

## Consequences

- On Cloud, a project's recordings are in VoiceGateway's bucket unless it brought one. Self-hosted installs keep using their configured bucket, which is the user's own, as 0008 intended.
- A project's bucket is fixed when it is created: createProject and `voicegateway project add` take it, Postgres keeps it in `project_buckets` with its secret sealed like the LiveKit secret, and a trigger refuses any update. Recordings never move between buckets, so there is no migration.
- The bucket is proven before the project is stored, with a write and a delete of one empty object through the worker's address guard, since a wrong bucket could never be corrected.
- Retention (90 days) runs on the instance's bucket and on every bucket a project brought.
- ARCHITECTURE.md's audio rule reads: audio is stored only when a project turns recordings on, in the bucket the project was created with.
