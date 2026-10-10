# 0002: Meter native framework instances through attach()

- Status: accepted
- Date: 2026-07-22 (recorded 2026-10-10)

## Context

The first versions exposed a `Gateway` and `ModelId` factory: users built providers through VoiceGateway, which also shipped provider plugins as extras. That coupled VoiceGateway's release cycle to every provider SDK and forced users to rewrite their agents to adopt it.

## Options

1. Keep the factory surface: full control of each call, heavy adoption cost and a long dependency tail.
2. Observe the frameworks the user already runs: `attach(session)` subscribes to LiveKit or Pipecat metrics, `guard()` wraps a single instance for budget, fallback and rate limit.

## Decision

Option 2, shipped in the framework-agnostic reshape (PRs #136 and #138). The public API is `attach`, `guard`, `Observer`, `register_worker`, `inference` and `__version__`.

## Consequences

- Adoption is one line, and provider plugins are installed in the user's agent, never as VoiceGateway extras.
- `import voicegateway` must not import LiveKit or Pipecat; frameworks load lazily by target type. Architecture tests pin this.
- Pricing is by model id, so it depends on the catalog in 0003.
- VoiceGateway sees only what the framework reports, so gaps in framework metrics are gaps in the product.
