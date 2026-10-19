# 0032: Ingestion selects a project before checking LiveKit credentials

- Status: implementation in progress
- Date: 2026-10-19

## Problem

The global LiveKit key constraint lets a tenant reserve another tenant's key string. Calling ListRooms at a caller-supplied URL does not prove ownership: a caller-controlled public server can return success without validating the token. The address guard prevents private-network access, not a fake public server.

## Decision

This supersedes the globally unique LiveKit key lookup from decision 0016.

Ingestion endpoints identify an organisation and project explicitly. The receiver loads only that project's key and secret and uses LiveKit's native JWT and webhook verification. A LiveKit key string is not globally unique across independently operated LiveKit servers. Project creation must not reserve it across organisations.

Use a scoped observability base URL and a scoped webhook URL. LiveKit Agents appends its observability paths to LIVEKIT_OBSERVABILITY_URL, including any path prefix. Verified against livekit/agents commit 831249a05a5d65d1616de4a6ac87073ad5e8f9d6, telemetry/traces.py, through the LiveKit docs MCP. The Go SDK v2.19.0 ListRooms uses the roomList grant; that read remains a connectivity check, never the tenant-isolation boundary.

## Required verification before release

- Two organisations can register the same key string with different secrets.
- A signed request sent to another project's URL fails and appends nothing.
- Webhook body hash and observability write grants remain verified by LiveKit libraries.
- Fake successful RoomService responses cannot reserve another tenant's key namespace.
- All upload kinds and recordings use the scoped resolver before writing data.
- Setup instructions and console copy provide the scoped URLs.
- Remove unscoped ingestion routing; do not keep a shared-instance fallback.

This change is not complete until route wiring, schema migration, documentation and end-to-end checks pass.

Migration ordering: 00011 removes global key uniqueness. Unmerged recordings and quota migrations must be renumbered to 00012 and 00013 respectively before integration; no migration already deployed on main is renumbered.
