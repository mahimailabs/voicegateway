# Phase 07a: Recordings stored and played

## Goal

A project that turns recordings on keeps the audio its LiveKit agents upload, in the install's S3 bucket, and anyone who may read the project plays it in the call view, seeking freely. Each recording is deleted 90 days after it is stored. Until now web read the audio part of every session upload and threw it away.

## Context

- Decision 0008: recordings are opt-in per project and ship in V1; VoiceGateway stores only the link, and audio lives in the bucket.
- Decision 0015: the agent's session upload (`recording.ogg`, stereo, caller on channel 0, agent on channel 1) comes first; web streams it into the bucket; a project with recordings off discards the audio and keeps the rest.
- Decision 0014: the upload path `/observability/recordings/v0` and its multipart body, as LiveKit Agents sends it (`livekit/agents/telemetry/traces.py`, `_upload_session_report`, read at commit a436d83): parts `header` (a `MetricsRecordingHeader`), `chat_history` and `audio`, in that order, each with its own `Content-Length`. The header's `start_time` is the recording's wall-clock start, the epoch when the agent has none.
- Decision 0031 (amends 0008): recordings live in the instance's bucket unless a project brings its own S3 bucket when it is created; that bucket never changes, so recordings never move.
- Decision 0026: the bundled bucket is SeaweedFS; VoiceGateway stays S3-generic through the MinIO Go client (`github.com/minio/minio-go/v7` v7.4.1, already in go.mod).
- The 90-day raw call data rule (ClickHouse migration 00010, #401).
- The contract already declares `getRecordingMedia`, `Call.recording`, the `recording_start` clock origin, listCalls' `recorded` filter, and `CallSummary.recorded` and `CallTotals.recorded`. This phase serves them; the contract does not change.
- The console's call view already plays `call.recording.url` with its audio player and shows "Recordings off" when there is none.

## In Scope

- **Opt-in.** `projects.recordings`, false by default (Postgres 00018). `voicegateway project recordings on|off --project <id> [--org <id>]`, and `make project-recordings PROJECT=<id> SET=on|off`. Off stops keeping new audio; what is stored stays until it expires.
- **Upload.** With recordings on, web streams the `audio` part to the bucket as it reads it, sized by the part's `Content-Length` when sent (one PUT, nothing held in memory), otherwise in 5 MiB parts. The key is `recordings/YYYY/MM/DD/<org>/<project>/<job id>.ogg`, by the UTC day web stored it; a job id that is not a plain name (`[A-Za-z0-9_-]{1,128}`) gets a random one. The session report on the stream carries the key, size and store time, never audio. An `audio` part before the `header` part is 400. A bucket that fails answers 503 with `RetryInfo`, the only 5xx LiveKit Agents retries, and appends nothing. With recordings off, nothing changes: the audio is discarded and the report says so.
- **Link.** Postgres `call_recordings` (call, object key unique, size, recording start, store time). The worker links a report's recording to the call the report joins; a redelivered report adds nothing. A recording whose report joins no call stays in the bucket until it expires.
- **Serve.** `getCall` returns `recording` with `url` (the same-origin `getRecordingMedia` path), `id`, `clockId: recording`, and a `recording` clock with origin `recording_start`, anchored at the reported start and its offset to the call start inferred (two hosts' clocks), or unknown with no reported start. `duration`, `segments` and `peaks` are null: nothing is extracted from the audio yet. The call read joins the latest recording in the same query, so getCall makes no extra round trip.
- **Media.** `getRecordingMedia` `GET /api/v1/org/{orgId}/project/{projectId}/recordings/{recordingId}`, Viewer, project scope, through `handle`. The id is looked up within the authorised project; the key is the stored one. `audio/ogg`, `Cache-Control: private, max-age=300`, byte ranges and conditional requests through `http.ServeContent` over the MinIO object, which reads the bucket by range. Unknown id, other project's id, expired or missing object: 404. Bucket down: 503 `source_unavailable`.
- **List.** `call_list` gains `recorded`; listCalls' `recorded` filter, each summary's `recorded` and the totals' `recorded` count read it. `recorded=true` no longer matches nothing (supersedes that line of phase 04d).
- **Retention.** Every worker, once an hour: delete `call_recordings` rows stored more than 90 days ago, then remove every bucket day prefix that ended at or before that cutoff, with multi-object deletes. An object lives at most a day past 90 days; a link never outlives its object. Safe on many workers at once. `getRecordingMedia` also refuses a recording past 90 days, so the hour between sweeps serves nothing expired.

- **Own bucket (0031).** createProject's optional `recordingBucket` (endpoint, bucket, region, access key id, secret access key) and `voicegateway project add --recording-bucket-endpoint --recording-bucket --recording-bucket-region --recording-bucket-access-key` with the secret from `RECORDING_BUCKET_SECRET_KEY`. The endpoint is an http or https URL with a host and no path; the bucket name follows S3's rules (minio-go's `s3utils`). Before anything is stored, a write and a delete of `recordings/voicegateway-check` signed with the given key, through the worker's address guard, 10 s at most: an S3 answer refusing either is 422 `recording_bucket_refused`, no S3 answer is 422 `recording_bucket_unreachable`, never saying why. `--skip-verify` skips only the LiveKit check. Postgres `project_buckets` (00018) holds it, written in the project's transaction, the secret sealed under its own context, and a trigger refuses every update. web stores and serves a project's recordings in its bucket, dialed through the same guard; every worker's retention also expires every brought bucket, one failing bucket not stopping the others. The console's Add project form has a switch for it, off by default.

## Out of Scope

- A recordings switch in the console, and per-project retention (the contract's `getProjectSettings`): a later phase, with its write API.
- Changing or removing a project's bucket after it is created (0031).
- Duration, waveform peaks and speaker segments from the Ogg audio.
- LiveKit Egress recordings (0008's second source) and the `recording-lost` alert.
- Embed tokens on `getRecordingMedia` (`x-embed-access`).

## Constraints

- Libraries: MinIO Go client v7.4.1 (already pinned) for every bucket call, `http.ServeContent` for ranges, sqlc and goose as before. No new dependency.
- web never writes ClickHouse; nothing here touches it.
- No audio, key or credential in a log line or on the stream.

## Acceptance Checks

1. Given a project with recordings on, when an agent uploads a session report with 1 MiB of audio, then the bucket receives every byte under the header's job id with the part's size, the stream carries the key and no audio, and the answer is 200 (`TestUploadStoresRecording`).
2. Given a stored recording, when it is opened and read from byte 400, then the 8 bytes match the upload (`TestRecordingRoundTripAndExpiry`, SeaweedFS 4.48).
3. Given a report delivered twice, then the call has one link with the header's start, and a report whose header start is the epoch has a null start (`TestSessionReportLinksRecording`); the call read carries the latest recording, another project's id is not found, and listCalls filters and counts recorded calls (`TestRecordingLinks`).
4. Given recordings stored 92 days ago, 90 days less an hour ago and now, when retention runs, then exactly the first is removed and the others still open (`TestRecordingRoundTripAndExpiry`); links before the cutoff are forgotten and later ones kept (`TestRecordingLinks`).
5. Given `getRecordingMedia`, then the whole audio is 200 and `bytes=5-7` is 206, both `audio/ogg` with `Accept-Ranges: bytes` and a private cache policy; unknown, other-project, expired and missing recordings are 404, a non-member is 403, and a bucket that is down is 503 (`TestGetRecordingMedia`).
6. Given a recorded call, then getCall's body matches the contract, with the recording's URL, a null duration and a `recording` clock whose offset is 1.5 s when the start is 1.5 s after the call's, or unknown without a start (`TestGetCallRecording`).
7. Given a bucket that is down, the answer is 503 with RetryInfo and nothing is appended; an audio part before the header is 400; with recordings off the bucket is never called (`TestUploadRecordingRefusals`, `TestUploadRecordingsOffDiscards`).

8. Given a bucket a project brings, when it is verified, then the right key passes, and a wrong secret, an unknown key or bucket is `ErrBucketRefused`, while a closed port or a loopback endpoint behind the public-only guard is `ErrBucketUnreachable` (`TestVerifyBucket`, SeaweedFS 4.48).
9. Given one project with its own bucket and one without, then each recording is stored and read in its project's bucket only, and retention expires both (`TestBucketsRouteByProject`).
10. Given a project created with a bucket, then the bucket reads back with its secret, the secret is not stored in the clear, an update is refused, a project without one has none, and a bucket the table refuses leaves no project (`TestProjectBucketFixedAtCreate`); createProject stores it only after the verifier passes, answers 422 `recording_bucket_refused` or `recording_bucket_unreachable` without the cause or secret, never asks the verifier when no bucket is brought, and is 403 with no verifier (`TestCreateProjectAnswersTheBucketVerifier`, `TestCheckProjectBucket`); the console sends the bucket only when switched on and clears its secret (`add-project.test.tsx`).

## Manual Check

Turn recordings on for a project, run a LiveKit agent with `record=True` and `LIVEKIT_OBSERVABILITY_URL` pointing at VoiceGateway, end the call, open it in the console and play and seek the recording.
