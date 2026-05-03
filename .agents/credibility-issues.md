# Credibility Issues

Inventory of `docs/` claims that don't match the code or current
external reality. Each entry: where the claim lives, what it says,
why it's wrong, and what should replace it. Phase 1.3 sweep
("docs/credibility-issues.md fix-list") works from this list.

Severity legend:
- **CRITICAL** — actively wrong, undermines trust on first read.
- **HIGH** — stale or misleading, will be discovered by sophisticated readers.
- **MEDIUM** — accurate-but-fragile, needs hedging or measurement.
- **LOW** — minor inconsistency / typo / formatting.

---

## CRITICAL

### C1. Runtime fallback over-promised (the "Cloud outage" claim)

The fallback subsystem only triggers on **resolver failure** at
`gw.stt_with_fallback()` / `gw.llm_with_fallback()` / `gw.tts_with_fallback()`
construction time (`voicegateway/middleware/fallback.py:21-79`).
Once a model is wired into a `LiveKit AgentSession`, runtime errors
*during* a call propagate to the user — VG does not swap providers
mid-call.

Affected files / lines (verbatim claims):

| File | Line | Claim |
|---|---|---|
| `README.md` | 237 | `Automatic failover across providers stay running even when a cloud provider has an outage.` |
| `README.md` | 255 | `If Deepgram returns 500s, requests automatically route to Groq. If both fail, local Whisper kicks in. Your agent never goes offline.` |
| `docs/index.md` | 46 | `Primary provider down? Gateway falls back automatically. Cloud outage? Switch to local. Your agent keeps running.` |
| `docs/examples/fallback-chains.md` | 3 | `Configure automatic failover between models so your voice agent stays available even when a provider goes down.` |
| `docs/examples/fallback-chains.md` | 178 | `ensuring your agent never goes completely offline` |
| `docs/examples/fallback-chains.md` | 193 | `This guarantees that even if all cloud providers are down, your agent can still function using local models.` |
| `docs/reference/changelog.md` | 39 | `Fallback chains -- per-modality automatic failover when providers are down` |
| `docs/architecture/middleware.md` | 160 | `Manages automatic failover between models within a modality.` (soft, but reads as runtime) |

**Fix direction (per design doc §5.4):** Reposition runtime fallback as
LiveKit `FallbackAdapter` territory. Phase 1.4 ships
`docs/examples/livekit-fallback-adapter.md`. Existing fallback-chains
doc should be reframed as "resolution-time fallback for warm-start
provider selection," with an explicit pointer to the LiveKit
FallbackAdapter guide for runtime/error-driven failover.

### C2. LiteLLM has STT and TTS — migration doc says it doesn't

LiteLLM (`docs.litellm.ai`, verified May 2026) ships
`/v1/audio/transcriptions` (Whisper, Deepgram, ElevenLabs Scribe)
and `/v1/audio/speech` (OpenAI, Azure, Gemini, ElevenLabs).

Affected lines in `docs/migration/from-litellm.md`:

| Line | Claim | Reality |
|---|---|---|
| 12 | `STT routing \| -- \| Deepgram, OpenAI Whisper...` | LiteLLM has STT; the `--` is wrong |
| 13 | `TTS routing \| -- \| Cartesia, ElevenLabs...` | LiteLLM has TTS; the `--` is wrong |
| 19 | `MCP server \| -- \| 17 tools...` | Verify whether LiteLLM has MCP support; if it ships one, this is also stale |
| 141 | `You now have unified cost visibility across STT, LLM, and TTS -- something LiteLLM cannot provide.` | LiteLLM CAN provide it now; it just doesn't tier per modality the way VG does |
| 153 | `When to stay with LiteLLM: ... You only need LLM routing (no voice workloads)` | Implies voice = VG-only; not true |
| 162 | `When to switch to VoiceGateway: You have STT and/or TTS workloads alongside LLM` | Same implication, stale |

**Fix direction (per design doc §5.5 and TODO 1.3):** Rewrite the
migration doc from competitive ("we're better at voice") to
complementary ("LiteLLM for general LLM gateway use; VoiceGateway
purpose-built for LiveKit voice agents"). Acknowledge LiteLLM's
audio endpoints exist. Reposition the wedge as "modality-aware unit
accounting + LiveKit-plugin return types + MCP" rather than "LiteLLM
has no voice."

### C3. Backup advice claims SQLite WAL mode that isn't enabled

`docs/reference/faq.md:175` instructs users:
> `cp ~/.config/voicegateway/voicegw.db ~/backups/voicegw-$(date +%Y%m%d).db`

with the parenthetical `(safe while gateway is running -- SQLite uses WAL mode)`.

Verified by grep: the only `PRAGMA` in `voicegateway/storage/sqlite.py`
is `PRAGMA table_info(requests)` (a read at line 130). No
`PRAGMA journal_mode = WAL` is set anywhere in the codebase.

Without WAL, copying the SQLite file while writes are in flight can
produce a torn / inconsistent backup. The advice is **dangerous**, not
just inaccurate.

**Fix direction:** EITHER explicitly enable WAL in
`voicegateway/storage/sqlite.py` initialization (`PRAGMA journal_mode = WAL`)
and keep the docs claim, OR change the docs to recommend
`sqlite3 voicegw.db ".backup voicegw.bak"` (which is safe regardless of
journal mode). Decision deferred to Phase 1.3 sweep author.

### C4. Dashboard `<title>` is the wrong product name

`dashboard/frontend/index.html:6` — `<title>LiveKit Inference Gateway</title>`.

This is not VoiceGateway; it's LiveKit's product name. Anyone who
opens the dashboard with the tab visible sees the wrong brand.

**Fix direction:** Replace with `VoiceGateway Dashboard` (or whatever
the new framing settles on). One-line edit. Cross-listed in
`framing-occurrences.md` Section 4.

### C5. Many docs use a model ID not in the pricing catalog

`voicegateway/pricing/catalog.py:19` lists
`anthropic/claude-3.5-sonnet`. Many docs reference
`anthropic/claude-sonnet-4-20250514` (a different identifier) and
`README.md:283` uses yet a third form `anthropic/claude-sonnet-4-6`.

Affected files using `claude-sonnet-4-20250514` (none of which the
pricing catalog will match — cost will compute as $0):

- `docs/guide/first-agent.md:29, 37, 144`
- `docs/guide/quick-start.md:126`
- `docs/guide/what-is-voicegateway.md:27`
- `docs/configuration/stacks.md:13`
- `docs/configuration/models.md:65, 114`
- `docs/configuration/voicegw-yaml.md:88, 108, 161`
- `docs/configuration/projects.md:65`
- `docs/api/python-sdk.md:142`
- `docs/examples/docker-deployment.md:72, 74, 92`

`README.md:283` uses `anthropic/claude-sonnet-4-6`. Inconsistent
with both other forms.

This is the single biggest "examples don't actually do what they
appear to" issue: a user follows the first-agent doc, sees zero
LLM cost in the dashboard, concludes cost tracking is broken.

**Fix direction:** Phase 2 will replace LLM pricing with
`pydantic/genai-prices` which carries 1,100+ models including
modern Claude families. After Phase 2, this self-resolves *if*
the model IDs in docs match what genai-prices recognizes. Until
then: either align all docs to `anthropic/claude-3.5-sonnet` (the
catalog entry), or pick one canonical newer Claude model and add
it to the catalog. **Task for Phase 1.3 sweep:** decide canonical
form, sweep all 13 occurrences.

Also note: `docs/examples/budget-enforcement.md:23, 47` uses
`whisper/large-v3` while the canonical catalog entry is
`local/whisper-large-v3` (`voicegw.example.yaml:66`,
`voicegateway/pricing/catalog.py:11`). Same class of bug, smaller
blast radius.

`docs/examples/fallback-chains.md:35` uses
`groq/llama-3.3-70b-versatile`; catalog has
`groq/llama-3.1-70b`. Same class of bug.

---

## HIGH

### H1. FAQ claims v0.1.0 "alpha" before v0.1.0 ships

`docs/reference/faq.md:5` says "VoiceGateway is currently at
v0.1.0 (alpha)." Current PyPI release is `0.0.3`. The line will be
correct once this Ralph loop ships the v0.1.0 tag, but is wrong
right now.

**Fix direction:** Leave as-is *if* this loop completes the v0.1.0
ship. Otherwise correct to `v0.0.3 (alpha)` until release. Mark for
re-verification in Phase 4 release-prep sweep (`TODO.md` 4.5).

### H2. FAQ test/coverage numbers don't match `pyproject.toml`

`docs/reference/faq.md:5` claims "200+ tests with over 70% code
coverage." `pyproject.toml:97` sets `fail_under = 75`. The README
badge says `tests-200+_passing`. So tests claim is consistent;
coverage claim is wrong by ≥5 points.

**Fix direction:** Change "over 70%" to "over 75%" or to the actual
current coverage number from a recent CI run.

### H3. Performance overhead claims are unmeasured

`docs/reference/faq.md:30-37`:
- "Routing resolution: microseconds (in-memory dict lookup)"
- "Cost tracking: ~1ms per request (async SQLite write)"
- "Budget check: ~1ms per request (async SQLite read)"
- "Latency monitoring: nanoseconds (timestamp diff)"
- "total overhead is typically under 5ms per request"

No benchmark file is referenced. No measurement source. These
numbers are guesses, presented as fact. A sophisticated reader
will run a benchmark and either confirm or refute.

**Fix direction:** EITHER add a real benchmark
(`tests/perf/test_overhead.py` or similar) and cite it, OR rewrite
in qualitative terms ("VG adds in-process middleware, not network
hops; overhead is bounded by a single SQLite write per logged
request"). Quantitative claims without measurement are credibility
debt. Out of scope for v0.1, mark for v0.2 backlog if not fixed.

### H4. Encryption env var name mismatch between two docs

- `docs/architecture/security.md:41` — uses `VOICEGW_SECRET` (correct, matches `voicegateway/core/crypto.py`).
- `docs/reference/troubleshooting.md:119` — refers to `VOICEGW_ENCRYPTION_KEY` env var. **This name does not exist in the codebase.**

A user hitting the troubleshooting page will set the wrong env var
and continue to see decryption errors.

**Fix direction:** Edit `troubleshooting.md:119` to use
`VOICEGW_SECRET`. One-line fix.

### H5. Migration doc claims fallbacks "automatically fail over when a provider is down"

`docs/migration/from-livekit-inference.md:166` —
> "VoiceGateway can automatically fail over when a provider is down"

Same C1 issue, in a different doc. Resolver-only.

**Fix direction:** Reword to "VoiceGateway can route to a backup
model if the primary cannot be resolved at startup; pair with
LiveKit's `FallbackAdapter` for runtime/error-driven failover during
a call. See [LiveKit FallbackAdapter integration](./examples/livekit-fallback-adapter.md)."

### H6. LiveKit Cloud Inference cost-comparison numbers are unverified

`docs/migration/from-livekit-inference.md:62-74` shows a cost
comparison table with specific numbers ($43, $3.75, $130 for
Deepgram/GPT-4o-mini/Cartesia at given volumes, vs. "Bundled in
LiveKit pricing"). LiveKit's pricing model can change; the table
has no "as of YYYY-MM-DD" attribution.

**Fix direction:** Add `> Pricing as of YYYY-MM-DD; verify against
the LiveKit dashboard before relying on these numbers.` Or remove
the table and replace with qualitative "you pay providers directly,
no inference markup" framing.

---

## MEDIUM

### M1. Multi-instance scaling advice glosses over budget cache divergence

`docs/reference/faq.md:43-79` Kubernetes example says
`replicas: 1  # SQLite requires single-writer` — correct. But the
follow-up at line 79 says:

> "If you need horizontal scaling, put a load balancer in front
> with sticky sessions, or use the Gateway as a library within
> each worker process (each gets its own DB)."

This is technically true but omits that **per-instance budget caches
diverge**: the audit (Section 2b) and design doc both flag this as
the structural ceiling. A user who runs two instances with separate
DBs cannot enforce a project-wide budget at all.

**Fix direction:** Add one sentence: "Note: separate DBs mean
budget enforcement is per-instance only. For project-wide budgets,
single-instance is currently the supported topology."
`docs/reference/troubleshooting.md:307-323` ("Database locked")
already gestures at this; cross-link.

### M2. PostgreSQL "support is planned"

`docs/reference/faq.md:233-234` —
> "Switch to a different storage backend (PostgreSQL support is planned)"

Per design doc §9, Postgres is **v0.3+** (not next-up). "Planned"
is too soft for "won't ship for ~6 months."

**Fix direction:** Edit to "PostgreSQL backend is v0.3 work; until
then VG runs on a single instance."

### M3. `docs/guide/first-agent.md` LiveKit prerequisites are thin

The audit flagged this; on re-read, prerequisites at lines 6-11 do
mention "A LiveKit server (local or cloud)" with a link, but no
explicit env-var setup (`LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`) and no inline command for spinning up
`livekit-server` locally. TODO task 1.3 #4 already calls this out.

**Fix direction:** Phase 1.3 task adds the explicit env-var setup
and both Cloud + self-hosted paths.

### M4. `docs/examples/fallback-chains.md` example imports non-existent class

Line 198 imports `from livekit.agents.voice_assistant import VoiceAssistant`.
LiveKit Agents 1.5+ replaced `VoiceAssistant` with `AgentSession` (the
modern API used everywhere else in this repo's examples). The example
will throw `ImportError: cannot import name 'VoiceAssistant'` for
anyone on `livekit-agents>=1.5.0` (which is the project's pinned
floor in `pyproject.toml:20`).

**Fix direction:** Rewrite the LiveKit Agent code block to use
`AgentSession` like `examples/basic_agent.py` does. Same fix
applicable in any other doc that imports `VoiceAssistant`.

---

## LOW

### L1. README badge "tests-200+_passing"

`README.md:11` has `tests-200+_passing` badge. The audit noted
`tests/` has ~200 tests collected. As tests are added, this badge
goes stale because it's a static SVG, not pulled from CI. Low
priority but worth knowing.

**Fix direction:** Replace with a dynamic badge from the
test-coverage workflow, OR remove it (the coverage badge alone
is sufficient signal).

### L2. Mermaid diagram in fallback-chains may mislead

`docs/examples/fallback-chains.md:88-98` shows a flowchart "Try X
→ on Error → Try Y." The visual implies runtime fallback. Even if
the surrounding prose corrects this, the diagram alone primes the
wrong mental model.

**Fix direction:** Either update the diagram to clarify "at
construction time" or label arrows as "if model resolution fails"
rather than "on error."

### L3. Migration version-upgrades doc is empty

`docs/migration/version-upgrades.md` exists but the audit noted
there's no actual migration story. Worth re-reading and either
fleshing out or marking explicitly "no breaking changes between
v0.0.x releases; v0.1.0 introduces …" once Phase 4 CHANGELOG is
written.

**Fix direction:** Phase 4.5 (CHANGELOG) should sync with this doc.

---

## Summary counts

- **CRITICAL: 5** issues (C1 - C5).
- **HIGH: 6** issues (H1 - H6).
- **MEDIUM: 4** issues (M1 - M4).
- **LOW: 3** issues (L1 - L3).
- **Total: 18 distinct issues across ~14 files.**

The audit predicted "3-5 more" beyond the two known. Found **16
more** (the original two were C1 and C2). Critical issues alone
are five — significantly more drift than the audit estimated.

## Cross-references

- Framing slip occurrences: see `.agents/framing-occurrences.md`.
- Design-doc resolution map: see `docs/design/v0.1.0.md` §5.5
  (framing fix scope) and §6 (Phase 1 deliverables).
- Audit context: see `docs/audit-2026-05-02.md` Section 4d
  (documentation completeness) and Section 6 (LiteLLM comparison).
