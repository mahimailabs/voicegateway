# VoiceGateway dashboard

The Phase 1 console: React 19, TypeScript, Vite, Tailwind v4 and shadcn/ui. It reads captured LiveKit data from `src/mocks/` until the web API exists; see `docs/specs/phase01-console.md`.

Run these commands from the repository root:

```sh
npm --prefix dashboard ci
make dev        # Vite dev server
make test       # helper regression tests
make lint       # oxlint + TypeScript
make typecheck  # tsc -b
make help       # list the targets
```

`npm run build` (inside `dashboard/`) runs the type check, then the production build.

`make test` runs the threshold-storage regression suite. Calls-list, call-detail and unknown-value UI tests remain tracked in issue #324. `make help` also lists the separate landing targets; they do not change what the dashboard commands run.

- `src/features/<page>/`: one folder per page.
- `src/components/`: shared pieces. `ui/` is shadcn, `reui/` is vendored ReUI (keep edits minimal), `data-table.tsx` is the list every page uses.
- `public/captures/` and `public/preview/` hold local recordings and are not committed.
