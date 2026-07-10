---
title: "Contributing to VoiceGateway"
description: "How to report bugs, suggest features, and submit pull requests to the VoiceGateway project."
---

# Contributing to VoiceGateway

Thank you for your interest in contributing to VoiceGateway. This guide covers everything you need to get started, whether you are reporting a bug, suggesting a feature, or submitting code.

## Code of Conduct

We follow the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). Please read it before participating. In short: be respectful, be constructive, and assume good intent.

## Ways to contribute

### Report a bug

1. Search [existing issues](https://github.com/mahimailabs/voicegateway/issues) to check if it has already been reported
2. Open a new issue using the **Bug Report** template
3. Include: VoiceGateway version (`voicegw --version`), Python version, OS, and a minimal reproducible example
4. Attach relevant logs (redact API keys)

### Suggest a feature

1. Open an issue using the **Feature Request** template
2. Describe the use case, not just the solution
3. If proposing a new provider, include links to the provider's API docs and pricing

### Submit a pull request

We welcome PRs for bug fixes, new providers, documentation improvements, and new features. Follow this process:

<Steps>
  <Step title="Fork the repository">
    Fork the repository on GitHub.
  </Step>
  <Step title="Create a branch">
    Use the naming convention:
    - `feat/<description>` for features
    - `fix/<description>` for bug fixes
    - `docs/<description>` for documentation
    - `test/<description>` for test-only changes
  </Step>
  <Step title="Set up your environment">
    Follow the [development setup guide](/contributing/development-setup).
  </Step>
  <Step title="Make your changes">
    Follow the [code style guide](/contributing/code-style).
  </Step>
  <Step title="Write tests">
    Cover any new or changed behavior. See the [testing guide](/contributing/testing).
  </Step>
  <Step title="Run the full test suite">
    `pytest` must pass.
  </Step>
  <Step title="Run linters">
    `ruff check` and `mypy` must pass.
  </Step>
  <Step title="Commit">
    Use [Conventional Commits](/contributing/code-style) format.
  </Step>
  <Step title="Open a PR">
    Open a PR against `main` with a clear description of what and why.
  </Step>
  <Step title="Respond to review feedback">
    Maintainers aim to review within 48 hours.
  </Step>
</Steps>

### Improve documentation

Documentation source lives in `docs/` and is rendered by Mintlify at `https://docs.voicegateway.dev`. Even small fixes (typos, broken links, clearer examples) are valuable.

## PR checklist

Before opening your PR, verify:

- [ ] Tests pass: `pytest`
- [ ] Linting passes: `ruff check .`
- [ ] Type checking passes: `mypy`
- [ ] New public APIs have Google-style docstrings
- [ ] Commit messages use Conventional Commits format
- [ ] Documentation is updated if behavior changed
- [ ] No secrets or API keys in the diff

## First-time contributors

Look for issues labeled [`good first issue`](https://github.com/mahimailabs/voicegateway/labels/good%20first%20issue). These are scoped, well-documented tasks suitable for newcomers. Common first contributions:

- Adding a new provider (follow the [provider guide](/contributing/adding-a-provider))
- Improving test coverage for an existing module
- Fixing a documentation gap
- Adding a pricing entry to the catalog

## Getting help

- Open a [GitHub Discussion](https://github.com/mahimailabs/voicegateway/discussions) for questions
- Tag `@mahimai` on issues if you are blocked

## Contributing pages

<CardGroup cols={2}>
  <Card title="Development Setup" href="/contributing/development-setup">
    Clone the repo, create a virtual environment, install dev dependencies, and run tests locally.
  </Card>
  <Card title="Adding a Provider" href="/contributing/adding-a-provider">
    Step-by-step guide to implementing a new provider that extends `BaseProvider`.
  </Card>
  <Card title="Testing" href="/contributing/testing">
    pytest setup, shared fixtures, async test patterns, and coverage expectations.
  </Card>
  <Card title="Code Style" href="/contributing/code-style">
    ruff, mypy, docstrings, Conventional Commits, and naming conventions.
  </Card>
  <Card title="Refreshing Pricing" href="/contributing/refreshing-pricing">
    How to update rates in `voice-prices` and bump the pin in VoiceGateway.
  </Card>
</CardGroup>
