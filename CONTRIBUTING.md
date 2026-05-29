# Contributing to VoiceGateway

Thank you for your interest in contributing. This file is the one-page
quick reference. Detailed guides live under
[`docs/contributing/`](docs/contributing/) and are rendered by the
in-tree Next.js + Fumadocs site (`web/`) at <https://voicegateway.mahimai.ca/docs>.

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
| Local environment, virtualenv, pre-commit | [docs/contributing/development-setup.md](docs/contributing/development-setup.md) |
| Running, writing, and debugging tests | [docs/contributing/testing.md](docs/contributing/testing.md) |
| Code style, ruff, mypy, naming, public-API contract | [docs/contributing/code-style.md](docs/contributing/code-style.md) |
| Adding a new provider | [docs/contributing/adding-a-provider.md](docs/contributing/adding-a-provider.md) |
| Refreshing STT and TTS pricing catalogs | [docs/contributing/refreshing-pricing.md](docs/contributing/refreshing-pricing.md) |

## Documentation

VoiceGateway owns the Markdown source content under `docs/` and the Next.js +
Fumadocs site that renders it (`web/`), published at
<https://voicegateway.mahimai.ca/docs>. Vercel auto-deploys the site on every
push to `main` (Root Directory `web/`, with an Ignored Build Step that skips
Python-only commits). To preview a doc change locally: `cd web && pnpm install
&& pnpm dev`. See `web/CLAUDE.md` for site-specific conventions.

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

docs/                  # Markdown docs source (rendered by web/)
web/                   # Next.js 16 + Fumadocs 16 marketing + docs site
install.sh             # one-line installer (curl|bash), repo root by convention
pyproject.toml
docker-compose.yml
```

## First time?

Start with [docs/contributing/development-setup.md](docs/contributing/development-setup.md)
to get your environment ready, then look for issues tagged
`good first issue` on GitHub.
