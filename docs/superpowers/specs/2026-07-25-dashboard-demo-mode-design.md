# Dashboard demo mode (voicegateway.dev/demo)

A no-login, read-only "command center" at `voicegateway.dev/demo`: the real
VoiceGateway dashboard, running against seeded static fixtures with no backend.
Top-of-funnel visibility, inspired by agnost.ai's no-login command center, but
showing what VoiceGateway actually measures (per-agent cost, the STT/LLM/TTS
latency split, fleet compute/memory, a cost trend) rather than an eval taxonomy
we have no data for.

## Approach: the real dashboard in "demo mode"

The demo IS the product on example data. Chosen over a native Next.js rebuild
(would reimplement dashboard visuals) or an embedded iframe (bolted-on). Lives in
the engine repo and versions with the dashboard code.

## Design

**Build-time flag.** `src/lib/demo.ts` exports `DEMO_MODE = import.meta.env.VITE_DEMO === '1'`.
Set only by `npm run build:demo` (VITE_DEMO=1). In the normal build the flag folds
to the literal `false` and rollup dead-code-eliminates every `if (DEMO_MODE)`
branch, so the real dashboard bundle ships none of the demo code or data.

**One interception point.** Every read funnels through `fetchJson()` in
`src/lib/api.ts`. In demo mode it dynamically `import('./demoFixtures')` and returns
seeded JSON — the dynamic import lives inside the dead branch, so the fixtures are
a separate chunk that only the demo build loads. `uploadBrandingLogo` (the one raw
fetch) is guarded too.

**No login gate.** The `/api/auth-status` fixture returns `auth_required: false`,
so `App.tsx` proceeds straight to `ready`.

**Honest + read-only.** A persistent "Demo data" banner (this is example data,
nothing billed or editable, with a "Get started free" CTA). Everything that writes
or bills is neutralized in demo mode:
- Probes: seeded agents report `probe.eligible: false` (reason: disabled in demo),
  so the play button self-disables and the auto-run never fires. The latency split
  is *seeded* via `latency_probe`, so cards still render it.
- The Agents 5s poll is skipped.
- Create/revoke API key, create/edit project + logo upload, delete rate-card rule,
  and run diagnostics controls are hidden. `demoFetch` throws on any mutating
  method as a backstop.

**Routing.** `BrowserRouter basename` is derived from `import.meta.env.BASE_URL`
(`/demo` for the demo build, empty otherwise), so client routes like `/demo/agents`
resolve.

**Build.** `vite.config.ts` sets `base: '/demo/'` and `outDir: 'dist-demo'` when
`VITE_DEMO=1`. `npm run build:demo` produces `dist-demo/` (gitignored). `npm run
dev:demo` runs it locally.

**Seeded data (`src/lib/demoFixtures.ts`).** A `demoFetch<T>(path, init)` router
covering every endpoint the pages read. Centerpiece is a 4-agent fleet with
distinct providers, costs, latencies, resources, and cached probes — including one
telemetry-only agent (null resources → honest "not sampled") and one errored probe,
so the honest empty/error states are on display, not hidden.

## Hosting (deploy step, owner: maintainer on Vercel)

The engine repo builds `dist-demo`; `voicegateway.dev/demo` serves it. Recipe:
copy `dist-demo/` into the marketing repo (`voicegateway-web`) `public/demo/`, and
add a Next.js rewrite so client routes fall back to the SPA shell:

```ts
// next.config.ts
async rewrites() {
  return [{ source: '/demo/:path((?!assets/|.*\\.).*)', destination: '/demo/index.html' }];
}
```

(The negative-lookahead keeps `/demo/assets/*` and file requests served directly.)
A CI step or a small `sync-demo` script copies the engine's `dist-demo` on each
update. Alternative: deploy `dist-demo` to a separate static target and rewrite
`/demo/:path*` to it — keeps the repos fully decoupled.

## Out of scope (v1)

Live data, interactivity beyond navigation, per-visitor state. The demo is a static
tour. Refreshing the numbers = re-seeding the fixtures in a PR.
