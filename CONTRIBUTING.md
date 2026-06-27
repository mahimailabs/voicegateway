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

The user-facing documentation lives in a separate repository,
[`mahimailabs/voicegateway-web`](https://github.com/mahimailabs/voicegateway-web):
a Next.js landing page published at <https://voicegateway.dev> and a Mintlify
docs site published at <https://docs.voicegateway.dev>. Documentation changes go
to that repo, not this one. This repository keeps only the brand assets under
`docs/assets/` (used by the README).

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
  dashboard/
    api/               # FastAPI dashboard backend
    frontend/          # React/TypeScript/Vite frontend
    Dockerfile         # dashboard runtime image
    README.dockerhub.md

docs/assets/           # brand assets used by the README (the docs site lives
                       # in the mahimailabs/voicegateway-web repo)
install.sh             # one-line installer (curl|bash), repo root by convention
collector.sh           # fleet collector installer (curl|bash)
pyproject.toml
docker-compose.yml
```

## First time?

Start with [docs/contributing/development-setup.md](docs/contributing/development-setup.md)
to get your environment ready, then look for issues tagged
`good first issue` on GitHub.
