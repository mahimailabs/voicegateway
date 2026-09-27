# Contributing to VoiceGateway

Thank you for your interest in contributing. This file is the one-page
quick reference. Detailed guides live in
[`contributing/`](contributing/).

## Code of Conduct

We follow the [Contributor Covenant](CODE_OF_CONDUCT.md). Be respectful,
be constructive, assume good intent.

## License and CLA

VoiceGateway is licensed under the [GNU Affero General Public License
v3.0](LICENSE). Before your first pull request can merge, sign the
[Contributor License Agreement](CLA.md): the CLA check comments on your pull
request with the one line to reply with. You sign once and keep the copyright
in your work. The CLA lets the project also be licensed for the hosted edition.

## Ways to contribute

- **Pick up a good first issue.** Issues labelled
  [good first issue](https://github.com/mahimailabs/voicegateway/issues?q=is%3Aopen+label%3A%22good+first+issue%22)
  name the files, the change and the test. Comment `.take` to claim one
  (first comment wins), then open a PR with `Closes #<issue>`.
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
| Local environment, virtualenv, pre-commit | [development-setup](contributing/development-setup.md) |
| Running, writing, and debugging tests | [testing](contributing/testing.md) |
| Code style, ruff, mypy, naming, public-API contract | [code-style](contributing/code-style.md) |
| Adding a new provider | [adding-a-provider](contributing/adding-a-provider.md) |
| Refreshing STT and TTS pricing catalogs | [refreshing-pricing](contributing/refreshing-pricing.md) |

## Documentation

The docs site at <https://docs.voicegateway.dev> is a [Fumadocs](https://fumadocs.dev)
app in `site/docs/`, statically exported and served from Cloudflare
(`site/docs/wrangler.jsonc`). Pages are MDX in `site/docs/content/docs/`,
ordered by `meta.json`; brand assets live in `site/docs/public/assets/`, and the
shared palette in `site/theme.css`. Docs version with the
code: change them in the same PR as any behaviour or API change.

The site is deliberately small while the project is early. Add a page only when
an existing one cannot hold the answer.

```bash
cd site/docs && npm ci
npm run dev     # http://localhost:3000/docs
npm run build   # the static export in site/docs/out, the same command CI runs
```

`.github/workflows/site.yml` builds the site and fails on any em dash in the
pages; the voice is short sentences, colons and commas.

The landing page at <https://voicegateway.dev> lives here too, in `site/web/`
(Astro on Cloudflare Workers); see `site/web/README.md`. Both sites share the
palette in `site/theme.css`, and `.github/workflows/site.yml` builds both.
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
site/docs/             # the Fumadocs site (docs.voicegateway.dev): MDX pages in
                       # content/docs/, brand assets in public/assets/
site/web/              # the landing page (voicegateway.dev), Astro on Workers
site/theme.css         # the palette both sites share
contributing/          # contributor guides: setup, tests, style, providers
specs/                 # internal design specs, not published
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

Start with [contributing/development-setup.md](contributing/development-setup.md)
to get your environment ready, then look for issues tagged
`good first issue` on GitHub.
