# Contributing to VoiceGateway

Thank you for your interest in contributing. This file is the one-page
quick reference. Detailed guides live in the documentation site at
<https://docs.voicegateway.dev/contributing>.

## Code of Conduct

We follow the [Contributor Covenant](CODE_OF_CONDUCT.md). Be respectful,
be constructive, assume good intent.

## Ways to contribute

- **Report a bug.** Open an issue using the **Bug Report** template.
  Include VoiceGateway version (`voicegw --version`), Python version,
  OS, and a minimal reproducible example. Redact API keys in any log
  excerpts.
- **Suggest a feature.** Open an issue using the **Feature Request**
  template. Describe the use case, not just the solution.
- **Submit a pull request.** Fork, branch (`feat/<desc>`,
  `fix/<desc>`, `docs/<desc>`, `test/<desc>`), commit, and open the PR
  against `main` with a clear description of what and why. We aim to
  review within 48 hours.
- **Report a security issue.** Do NOT open a public issue. See
  [SECURITY.md](SECURITY.md) for the disclosure policy.

## PR checklist

Before opening your PR, verify locally:

- [ ] `pytest` -- full suite green
- [ ] `ruff check .` -- linting clean
- [ ] `mypy` -- type checking clean
- [ ] New public APIs have Google-style docstrings
- [ ] Commit messages follow Conventional Commits
- [ ] Documentation updated if behavior changed
- [ ] No secrets or API keys in the diff

## Deeper guides

| Topic | Doc |
|---|---|
| Local environment, virtualenv, pre-commit | [development-setup](https://docs.voicegateway.dev/contributing/development-setup) |
| Running, writing, and debugging tests | [testing](https://docs.voicegateway.dev/contributing/testing) |
| Code style, ruff, mypy, naming, public-API contract | [code-style](https://docs.voicegateway.dev/contributing/code-style) |
| Adding a new provider | [adding-a-provider](https://docs.voicegateway.dev/contributing/adding-a-provider) |
| Refreshing STT and TTS pricing catalogs | [refreshing-pricing](https://docs.voicegateway.dev/contributing/refreshing-pricing) |

## Documentation

The Mintlify docs site published at <https://docs.voicegateway.dev> lives in
**this** repository under `docs/`: config in `docs/docs.json`, pages as `.md`,
brand assets under `docs/assets/`. Mintlify deploys `docs/` from the default
branch, so docs version with the code. Change the docs in the same PR as any
behaviour or API change.

Two things to know before editing:

- `docs/_check_docs.py` is a real gate, enforced in CI by
  `.github/workflows/docs.yml`. It fails any page not wired into the `docs.json`
  nav, so adding a file is never enough on its own. It skips `superpowers/`,
  `snippets/`, and any dot- or underscore-prefixed path.
- Run it locally with `python3 docs/_check_docs.py`, the same command CI uses.

Only the Next.js landing page at <https://voicegateway.dev> lives elsewhere, in
[`mahimailabs/voicegateway-web`](https://github.com/mahimailabs/voicegateway-web).
This repository has no Vercel connection.

## Project layout (quick orientation)

```
src/
  voicegateway/        # Python package (subpackages only at top level)
    cli/               # voicegw CLI commands and Textual TUI
    core/              # gateway orchestrator, config, router
    data/              # bundled resource files (voicegw.example.yaml)
    inference/         # LiveKit-Cloud-parity STT / LLM / TTS factories
    mcp/               # MCP server + tools
    middleware/        # cost tracking, latency, rate limiting, fallback,
                       # routing, guardrails
    pricing/           # voice-prices wrappers (LLM, STT, TTS)
    providers/         # 11 provider adapters (cloud + local)
    reconcile/         # provider-invoice reconciliation
    server/            # FastAPI HTTP API + combined server
    storage/           # SQLite backend with versioned migrations
    tests/             # pytest suite mirroring the subpackages above;
                       # excluded from the wheel via pyproject hatch config.
                       # Hosts tests/fixtures/streaming/record_streaming_fixtures.py,
                       # the dev-only fixture recorder.
    Dockerfile         # gateway runtime image (build context: repo root)
    README.dockerhub.md # DockerHub-only description, published on release
  dashboard/           # no routes live here; the server above serves these
    api/static/        # branding images only
    frontend/          # React/TypeScript/Vite dashboard SPA
    console/           # smaller SPA built on @openorca-ui/react
    Dockerfile         # dashboard runtime image
    README.dockerhub.md

alembic/               # migration environment and versions. Root, not under
alembic.ini            # src/: pyproject force-includes it into the wheel.
docs/                  # the Mintlify docs site (docs.voicegateway.dev),
                       # config in docs.json, brand assets in docs/assets/
examples/              # runnable files that docs/examples/*.md link to by
                       # blob URL; moving them breaks published links
deploy/
  prober/              # Fly.io deploy target (Dockerfile + fly.toml),
                       # documented in docs/deployment/distributed-sfu.md
  grafana/             # importable Grafana dashboard for the load test.
                       # GENERATED by voicegateway.loadtest.dashboard; a test
                       # fails if the checked-in JSON drifts from the generator
tools/                 # developer tooling, none of it shipped in the wheel
  mock-participant/    # Go module: a LiveKit agent worker used to place load
  scripts/             # build_wheel.sh (run by the publish workflow) and
                       # e2e-frontend-gate.sh (manual frontend gate)
  benchmarks/          # decision-record perf scripts, run by hand, not in CI

install.sh             # one-line installer (curl|bash), repo root by convention
collector.sh           # fleet collector installer (curl|bash)
docker-compose.yml     # single-container API + dashboard
docker-compose.collector.yml    # Postgres-backed fleet collector stack
docker-compose.autoupdate.yml   # opt-in overlay, layered with -f on the above.
                       # These three stay at the root: docs hand users raw
                       # githubusercontent URLs pointing at them.
pyproject.toml
```

## First time?

Start with [docs/contributing/development-setup.md](docs/contributing/development-setup.md)
to get your environment ready, then look for issues tagged
`good first issue` on GitHub.
