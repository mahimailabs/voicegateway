# 0001: Open-core license, MIT core with a commercial ee/

- Status: accepted
- Date: 2026-10-10

## Context

VoiceGateway is public and was MIT. A hosted cloud edition is coming, and some future features (organization-level access control, SSO, audit retention) may need to be commercial. The license has to keep the self-hosted profiler fully open while leaving room for that.

## Options

1. AGPL for the whole repository: protects against hosted forks, but scares off companies that would embed `attach()` in their agents.
2. MIT everywhere: maximum adoption, no room for commercial features in the same repository.
3. MIT for everything outside `ee/` directories, a commercial Enterprise Edition license inside them, following Langfuse and PostHog.

## Decision

Option 3. Root `LICENSE` is MIT Expat with an `ee/` carve-out; `ee/LICENSE` holds the EE terms (PR #313, replacing the AGPL proposal in #306).

## Consequences

- All metering, pricing and profiling stays MIT. The self-hosted core must work without any `ee/` code.
- Commercial code may only live under an `ee/` directory, and no MIT code may import from `ee/` in a way that makes the core depend on it.
- Past MIT releases stay MIT.
- Moving a feature into `ee/` is a product decision for the maintainer, recorded in a new decision.
