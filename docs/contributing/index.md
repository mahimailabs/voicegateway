---
title: "Contributing to VoiceGateway"
description: "How to report bugs, suggest features, and submit pull requests to the VoiceGateway project."
---
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
3. If proposing a new provider, link the provider's API docs and pricing page. Most provider requests are actually a [pricing entry](/contributing/adding-a-provider), not a code change.

### Submit a pull request

<Steps>
  <Step title="Fork and branch">
    Naming convention: `feat/<description>`, `fix/<description>`, `docs/<description>`, `test/<description>`.
  </Step>
  <Step title="Set up your environment">
    Follow [Development Setup](/contributing/development-setup).
  </Step>
  <Step title="Make your changes">
    Follow [Code Style](/contributing/code-style).
  </Step>
  <Step title="Write tests">
    Cover new or changed behavior. See [Testing](/contributing/testing).
  </Step>
  <Step title="Run the full suite">
    `pytest` and `ruff check .` must pass locally; `mypy` runs in CI (see [Code Style](/contributing/code-style) for the pinned version).
  </Step>
  <Step title="Commit">
    Use [Conventional Commits](/contributing/code-style#conventional-commits).
  </Step>
  <Step title="Open a PR against main">
    Describe what changed and why.
  </Step>
</Steps>

### Improve documentation

Docs source lives in `docs/` in this repo and is rendered by Mintlify at `https://docs.voicegateway.dev`. Even small fixes (typos, broken links, clearer examples) are welcome.

## PR checklist

- [ ] `pytest` passes
- [ ] `ruff check .` passes
- [ ] `mypy` passes (see [Code Style](/contributing/code-style))
- [ ] New public APIs have Google-style docstrings
- [ ] Commit messages use Conventional Commits format
- [ ] Docs are updated in the same PR if behavior changed
- [ ] No secrets or API keys in the diff

## First-time contributors

Look for [`good first issue`](https://github.com/mahimailabs/voicegateway/labels/good%20first%20issue). Common starting points:

- Adding or verifying a [voice-prices pricing entry](/contributing/adding-a-provider) for a model
- Improving test coverage for an existing module
- Fixing a documentation gap

## Getting help

- [GitHub Discussions](https://github.com/mahimailabs/voicegateway/discussions) for questions
- Tag `@mahimai` on an issue if you're blocked

## Contributing pages

<CardGroup cols={2}>
  <Card title="Development Setup" href="/contributing/development-setup">
    Clone, install, and run the test suite locally.
  </Card>
  <Card title="Adding a Provider" href="/contributing/adding-a-provider">
    Add a voice-prices entry so a provider/model resolves to a cost.
  </Card>
  <Card title="Testing" href="/contributing/testing">
    pytest fixtures, async patterns, and coverage expectations.
  </Card>
  <Card title="Code Style" href="/contributing/code-style">
    ruff, mypy, docstrings, Conventional Commits, naming conventions.
  </Card>
  <Card title="Refreshing Pricing" href="/contributing/refreshing-pricing">
    Update rates in voice-prices and bump the pin in VoiceGateway.
  </Card>
</CardGroup>
