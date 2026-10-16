# Contributing to VoiceGateway

## Code of Conduct

We follow the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/): be respectful, be constructive, assume good intent.

## Ways to contribute

### Report a bug

1. Search [existing issues](https://github.com/mahimailabs/voicegateway/issues) first.
2. Open a new issue with the **Bug Report** template.
3. Include the VoiceGateway version (`voicegw --version`), Python version, OS, and a minimal reproducible example.
4. Redact API keys from any attached logs.

### Suggest a feature

1. Open an issue with the **Feature Request** template.
2. Describe the use case, not just the solution.
3. If proposing a new provider, link the provider's API docs and pricing page. Most provider requests are actually a [pricing entry](adding-a-provider.md), not a code change.

### Submit a pull request
### Fork and branch
Naming convention: `feat/<description>`, `fix/<description>` or `chore/<description>`.
### Set up your environment
Follow [Development Setup](development-setup.md).
### Make your changes
Follow [Code Style](code-style.md).
### Write tests
Cover new or changed behavior. See [Testing](testing.md).
### Run the full suite
`pytest` and `ruff check .` must pass locally; `mypy` runs in CI (see [Code Style](code-style.md) for the pinned version).
### Commit
Use [Conventional Commits](https://docs.voicegateway.dev/docs).
### Open a PR against main
Describe what changed and why.
### Improve documentation

Docs source lives in `site/docs/` in this repo and is published at `https://docs.voicegateway.dev`. Even small fixes (typos, broken links, clearer examples) are welcome.

## PR checklist

- [ ] `pytest` passes
- [ ] `ruff check .` passes
- [ ] `mypy` passes (see [Code Style](code-style.md))
- [ ] New public APIs have Google-style docstrings
- [ ] Commit messages use Conventional Commits format
- [ ] Docs are updated in the same PR if behavior changed
- [ ] No secrets or API keys in the diff

## First-time contributors

Look for [`good first issue`](https://github.com/mahimailabs/voicegateway/labels/good%20first%20issue). Common starting points:

- Adding or verifying a [voice-prices pricing entry](adding-a-provider.md) for a model
- Improving test coverage for an existing module
- Fixing a documentation gap

## Getting help

- [GitHub Discussions](https://github.com/mahimailabs/voicegateway/discussions) for questions
- Tag `@mahimai` on an issue if you're blocked

## Contributing pages
- [Development Setup](development-setup.md):
Clone, install, and run the test suite locally.
- [Adding a Provider](adding-a-provider.md):
Add a voice-prices entry so a provider/model resolves to a cost.
- [Testing](testing.md):
pytest fixtures, async patterns, and coverage expectations.
- [Code Style](code-style.md):
ruff, mypy, docstrings, Conventional Commits, naming conventions.
- [Refreshing Pricing](refreshing-pricing.md):
Update rates in voice-prices and bump the pin in VoiceGateway.
