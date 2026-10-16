# 0026: SeaweedFS for the bundled S3 bucket

- Status: accepted (confirmed by the maintainer on 2026-10-16)
- Date: 2026-10-16

## Context

Decision 0005 ships six services, the sixth being "MinIO (or any S3 bucket)". Building the Compose stack on 16 October, MinIO's images were no longer available: `minio/minio` on Docker Hub and `quay.io/minio/minio` both return nothing, because MinIO stopped publishing free images. The stack pins every image by tag and digest (CLAUDE.md Versions), so it needs a maintained, published image.

## Options

1. A third-party rebuild of MinIO (for example `pgsty/minio`): the same server, but packaged by someone else with no commitment to keep publishing.
2. SeaweedFS (`chrislusf/seaweedfs`, Apache 2.0): versioned images published weekly, and `weed mini` runs a single-node S3 endpoint that creates its bucket and key pair from environment variables.
3. RustFS: MinIO-compatible, but its current releases are previews.

## Decision

Option 2. The bundled bucket in `deploy/compose.yaml` is SeaweedFS `weed mini`. VoiceGateway itself stays S3-generic through the MinIO Go client, so any S3 bucket (AWS S3, Cloudflare R2, MinIO) still works by setting `VOICEGATEWAY_S3_*`.

## Consequences

- The Compose service is called `s3`, not `minio`; credentials come from `deploy/.env`, created with random values by `make up`.
- ARCHITECTURE.md and CLAUDE.md name "S3 (SeaweedFS in Compose)" instead of MinIO.
- Tests run the same pinned SeaweedFS image, so a change of store is one image line.
- Verified in CI on PR #336: `weed mini` created the `recordings` bucket and key pair from its environment, passed its `/healthz` check in Compose, and answered preflight's BucketExists with the generated credentials.
