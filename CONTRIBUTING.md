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

The contract is `docs/api/openapi.yaml` and `make api-check` validates it. Do not treat captured console data as an operational backend.

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

`make test` and `make lint` run the Go and dashboard checks together. `make up` also applies database migrations. See [docs/progress.md](docs/progress.md) for the current phase.

## Choose and submit work

Pick an [open issue](https://github.com/mahimailabs/voicegateway/issues), agree the scope, and use `.take` where the issue invites it. Branches use `feat/`, `fix/` or `chore/`. Open a focused PR against `main`, describing the observable change and validation. Keep generated files reproducible, preserve unknown values and include meaningful behaviour/failure tests.

Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Report vulnerabilities privately through [SECURITY.md](SECURITY.md). Do not commit credentials, real customer data or unredacted captures.

Contributions outside `ee/` follow [LICENSE](LICENSE); Enterprise code follows [ee/LICENSE](ee/LICENSE). Keep those boundaries intact. Attribute adapted components and preserve their licences.

## Sites and project tools

- `site/docs/`: the Fumadocs application and its own build commands.
- voicegateway.dev and its waitlist API live in the private `voicegateway-web` repository (decision 0030).
- `.mcp.json`: the shared V1 project tooling.
