# Contributing to VoiceGateway

Start with [PRODUCT.md](PRODUCT.md), [ARCHITECTURE.md](ARCHITECTURE.md), the [current console spec](docs/specs/phase01-console.md) and [progress](docs/progress.md). Agent rules live in [CLAUDE.md](CLAUDE.md); AGENTS.md points to it.

## V1 console

Run these commands from the repository root with Node 24:

```sh
npm --prefix dashboard ci
make dev
make dashboard-test
make dashboard-lint
npm --prefix dashboard run build
make help
```

The current tests cover threshold helpers. Calls-list, call-detail and unknown-value UI tests remain [#324](https://github.com/mahimailabs/voicegateway/issues/324). The API contract is `docs/api/openapi.yaml` (#321, done); generated types and contract-backed mocks remain #322 and #323. Do not treat captured console data as an operational backend.

The [Connect execution plan](docs/specs/v1-connect-plan.md) explains dependencies, proposed PR slices, repository ownership and the legacy retirement boundary. The Go core and Python SDK paths described there are targets; create them as working code arrives.

## V1 platform (Go)

With Go 1.26 and Docker, from the repository root:

```sh
make up         # the six-service stack; creates deploy/.env with random secrets on first run
make preflight  # checks Postgres, ClickHouse, Redis and the S3 bucket answer
make go-test    # Go tests with the race detector; container tests need Docker
make go-lint    # golangci-lint, gofumpt/goimports, go mod tidy and sqlc output
LIVEKIT_URL=wss://… LIVEKIT_API_KEY=… LIVEKIT_API_SECRET=… make project-add NAME="My project"
make down
```

`make test` and `make lint` run the Go and dashboard checks together. `make up` also applies database migrations. The current phase is [phase03-first-call](docs/specs/phase03-first-call.md).

## Choose and submit work

Pick an [open issue](https://github.com/mahimailabs/voicegateway/issues), agree the scope, and use `.take` where the issue invites it. Branches use `feat/` or `fix/`. Open a focused PR against `main`, describing the observable change and validation. Keep generated files reproducible, preserve unknown values and include meaningful behaviour/failure tests.

Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Report vulnerabilities privately through [SECURITY.md](SECURITY.md). Do not commit credentials, real customer data or unredacted captures.

Contributions outside `ee/` follow [LICENSE](LICENSE); Enterprise code follows [ee/LICENSE](ee/LICENSE). Keep those boundaries intact. Attribute adapted components and preserve their licences.

## Sites and project tools

- `site/docs/`: the Fumadocs application and its own build commands.
- voicegateway.dev and its waitlist API live in the private `voicegateway-web` repository (decision 0030).
- `.mcp.json`: the shared V1 project tooling. The [legacy MCP example](docs/contributing/mcp-legacy.example.json) is optional reference for the published Python CLI; merge any needed entries rather than overwriting the V1 tool configuration.

## Published Python release

The Python runtime in `src/` and its original frontend remain supported while V1 is built. Changes to that release must use the [legacy development guide](docs/contributing/development-setup.md), [testing guide](docs/contributing/testing.md) and [code style guide](docs/contributing/code-style.md). Provider and pricing work is documented in the [guide index](docs/contributing/index.md).

Keep root packaging, Alembic, Compose, public installer paths and their CI intact until a deliberate replacement release retires them. Moving or deleting them as cosmetic cleanup can break existing installations and package builds.
