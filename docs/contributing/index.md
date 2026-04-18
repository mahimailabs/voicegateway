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

1. **Fork the repository** on GitHub
2. **Create a branch** from `main` using the naming convention:
   - `feat/<description>` for features
   - `fix/<description>` for bug fixes
   - `docs/<description>` for documentation
   - `test/<description>` for test-only changes
3. **Set up your [development environment](/contributing/development-setup)**
4. **Make your changes** following the [code style guide](/contributing/code-style)
5. **Write tests** for any new or changed behavior (see [testing guide](/contributing/testing))
6. **Run the full test suite** -- `pytest` must pass
7. **Run linters** -- `ruff check` and `mypy` must pass
8. **Commit with [Conventional Commits](/contributing/code-style#conventional-commits)** format
9. **Open a PR** against `main` with a clear description of what and why
10. **Respond to review feedback** -- maintainers aim to review within 48 hours

### Improve documentation

Documentation lives in `docs/` and uses VitePress. See [development setup](/contributing/development-setup#documentation-site) for running the docs site locally. Even small fixes (typos, broken links, clearer examples) are valuable.

## PR checklist

Before opening your PR, verify:

- [ ] Tests pass: `pytest`
- [ ] Linting passes: `ruff check .`
- [ ] Type checking passes: `mypy voicegateway`
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

## Related pages

- [Development Setup](/contributing/development-setup)
- [Adding a Provider](/contributing/adding-a-provider)
- [Code Style](/contributing/code-style)
- [Testing](/contributing/testing)
- [Troubleshooting](/reference/troubleshooting)
