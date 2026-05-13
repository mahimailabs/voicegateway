# Agency quickstart (v0.5.0)

VoiceGateway v0.5.0 ships the agency rung of the buyer ladder: cross-modality routing and per-project white-label branding. This guide walks an agency operator through provisioning a downstream customer project end-to-end.

## Prerequisites

- VoiceGateway v0.5.0 or later (`voicegw --version`).
- Dashboard running: `voicegw dashboard` (defaults to `127.0.0.1:9090`).
- `voicegw.yaml` configured with at least one project (operators usually have a `default` plus one per customer).

## 1. Configure routing rosters and budget

Open `voicegw.yaml` and add a `routing:` block under the customer's project. The router will only pick providers that appear in the rosters; order is preference (earlier first when two candidates tie on predicted latency).

```yaml
projects:
  acme:
    name: Acme Voice
    daily_budget: 25.0
    routing:
      # OQ1 lock: 1500 ms is the typical conversational target.
      budget_ms: 1200          # Agency wants tighter than default.
      fallback_to_fastest: true
      rosters:
        stt: [deepgram, assemblyai]
        llm: [groq, openai]
        tts: [cartesia, elevenlabs]
```

After editing, restart the gateway. The next session start picks providers from the new rosters; in-flight sessions keep their pre-existing triple (AC-VG-ROUTE-001.3).

### Pick a budget

The default 1500 ms covers a typical conversational voice agent: caller stops talking, agent's first audible reply lands in ≈1.5 seconds. Agencies serving high-energy customer-service scenarios often tighten to 800–1000 ms; agencies serving deliberative legal or medical scenarios may relax to 2000 ms. The dashboard's Routing view shows actual observed latency per provider so an operator can right-size after a few hundred sessions.

## 2. Verify the router will pick what you expect

The CLI's `route` subgroup is read-only and useful for sanity-checking before traffic lands.

```bash
voicegw route show acme
# Project acme  budget_ms=1200
#
# Rosters
#   stt   deepgram, assemblyai
#   llm   groq, openai
#   tts   cartesia, elevenlabs
#
# (no observations yet — router will fall back to provider_baselines.json)
```

```bash
voicegw route simulate acme
# Project acme simulated route:
#   STT: deepgram     (250 ms baseline)
#   LLM: groq         (80 ms baseline)
#   TTS: cartesia     (150 ms baseline)
#   Predicted total: 480 ms
#   Under budget (1200 ms): yes
```

```bash
voicegw route simulate acme --llm openai  # Override LLM specifically.
# LLM: openai (300 ms baseline)
# Predicted total: 700 ms
```

After production traffic accrues, the rollup worker (every 15 minutes) populates `latency_observations` and the router prefers observed p50 over the curated baselines. `voicegw route show acme` then prints the live observations table.

## 3. Upload the customer's logo and brand

Open the dashboard at `http://127.0.0.1:9090/projects`, find the `acme` card, click **Brand**, and fill the modal:

- **Product name**: e.g. `AcmeVoice` (up to 64 chars; appears in the sidebar in place of "VoiceGateway").
- **Accent color**: `#FF6633` or any valid hex (the dashboard offers a native color picker too).
- **Logo**: a PNG or SVG file. Maximum 256 KB, max dimensions 512x512 px for PNG (SVG is vector, no dimension check). Saved under `src/dashboard/api/static/branding/acme.{png,svg}` and served at `/static/branding/acme.png`.

For scripted provisioning, the CLI's `brand` subgroup hits the same endpoints:

```bash
voicegw brand set \
  --project acme \
  --logo ./acme-logo.png \
  --accent "#FF6633" \
  --name AcmeVoice
# Project acme branding updated:
#   Logo:         /static/branding/acme.png
#   Accent color: #FF6633
#   Product name: AcmeVoice
```

Set `VOICEGW_API_KEY=...` to pass the static-key Bearer header when the dashboard requires auth.

## 4. Send the customer a branded link

Branding is per-project; the dashboard picks up the active project from the URL query parameter. Share `https://your-gateway/sessions?project=acme` with the customer and they see the AcmeVoice brand: sidebar logo, accent color on interactive elements, page title and favicon. Without `?project=acme` the default VoiceGateway brand renders.

The branding cache is per-mount per OQ5: a customer who has the dashboard open during a brand change sees the new look on next page navigation, not in real time.

## 5. Watch the Routing view as traffic lands

Open `/routing` in the dashboard. The page shows per-provider p50/p95 and sample count for every project the gateway has seen sessions for. Use the column headers to sort by p50 ascending to spot the fastest provider in each modality, or by sample count to gauge confidence.

The page auto-refreshes every hour (AC-VG-ROUTE-003.3). The rollup worker behind the scenes refreshes every 15 minutes; the FE cadence is the page-side refresh, not the data freshness.

NULL p50 renders as "no observations yet" rather than zero (AC-VG-ROUTE-003.4) so it's obvious which entries the router is still relying on baselines for.

## 6. Inspect the routing decision per session

From the Sessions page, click any row to open the SessionDetail modal. The new routing strip shows:

- **STT / LLM / TTS** picked for the session.
- **Budget** that was in effect when the session started.
- **Actual end-to-end latency** when the close-session hook populated `budget_ms_used` (otherwise omitted).
- **budget_overrun chip** (yellow) when the router fell back to fastest because nothing fit the budget.

Sessions that predate v0.5.0 don't show the strip at all (the four routing columns are NULL).

## What v0.5.0 does not do

A few capabilities are deliberately deferred. Plan accordingly.

- **No mid-call routing.** Pick-at-start only. If a provider degrades mid-call, the session keeps that provider until close.
- **No adaptive learning.** The roll-up is static aggregation; there's no ML on in-call telemetry feeding back into pick scores.
- **No custom-domain dashboard hosting.** White-label sits at the gateway's own host; agencies pointing `dashboard.theirfirm.com` at the gateway with their own TLS cert is future scope.
- **No per-tenant branding inside one project.** White-label is per-project. An agency running multiple downstream tenants in one project shares one brand.
- **No email or exported-report branding.** Dashboard chrome only.
- **No cost-aware routing.** v0.5.0 picks on latency; cost is observed via the existing per-modality dashboards but doesn't feed back into the picker.
- **No multi-region routing.**
- **No latency-budget enforcement in flight.** The budget is a router input at start, not a runtime kill switch.
- **No performance SLAs from the gateway to the operator.** Best-effort prediction; the gap between prediction and reality is visible in the Routing view so operators can tune.

## Where the design lives

- **Refinery / Foundry**: REQ-VG-ROUTE-001..004 in the Linear project.
- **Migration**: `src/voicegateway/storage/migrations/0006_routing_and_branding.py`.
- **Router**: `src/voicegateway/middleware/router.py` + `latency_observations_worker.py`.
- **Baselines**: `src/voicegateway/core/provider_baselines.json`.
- **Storage**: `src/voicegateway/storage/latency_observations_repo.py`.
- **Dashboard API**: `src/dashboard/api/main.py` (search for `/api/routing` and `/api/projects/{id}/branding`).
- **Frontend**: `src/dashboard/frontend/src/pages/Routing.tsx`, `lib/branding.ts`, `pages/Sessions.tsx` (`RoutingStrip`), `pages/Projects.tsx` (`BrandingModal`).
- **CLI**: `src/voicegateway/cli/route.py`, `src/voicegateway/cli/brand.py`.
