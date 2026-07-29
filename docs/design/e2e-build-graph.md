# M0–M6 graph execution: autonomy analysis + prep

## 1. The verdict up front

**Autonomy is not gated by the graph. It is gated by whether each node has a trustworthy pass/fail signal.** You have two very different zones, measured:

| Zone | Nodes | Gate | Autonomy |
|---|---|---|---|
| **A — backend** | T1, T2, T3, T5, R1, C1, C2 | `ruff check src` → `uv run mypy` → `uv run pytest -q -m "not integration"` over **199 test files** | **High.** Run unattended. |
| **B — frontend** | V1, V2, V3, V4(ui), V5, L6 | `tsc -b` + `vite build` + `vite build --mode demo` — **compile only. Zero test runners** (no vitest/jest/playwright config) | **Low for correctness.** Code can compile, build, and render blank cards. |
| **C — real world** | L0, L1–L5 (M5), the §10 decisions | Needs a live SIP endpoint, a real AWS account, real money, or human judgment | **None.** HITL required. |

**The tension worth naming:** M0 has the best value/effort in the whole plan *and* the weakest gate — it is entirely frontend. The failure mode is already documented: `demoFixtures.ts`'s existing `DIAG_RUNS` are invented shapes matching nothing `RealProbes` returns, so typed rendering against them yields **blank cards that compile perfectly**.

## 2. Prep, in order (do all of §2 before starting any node)

### 2.1 Close the frontend verification gap — highest leverage item

Pick one. Without it, every frontend node needs a human eyeball and autonomy collapses.

- **(a) Add vitest + React Testing Library** and one render-smoke test per tab: *renders without throwing, given the real fixture shape*. Small investment, converts Zone B → Zone A.
- **(b) Use Playwright MCP** (you already have it — `.playwright-mcp/` holds 124 console logs). Build → serve → navigate → assert the card's text is non-empty → screenshot. Behavioral, no new dep.
- **(c) Accept HITL** on all frontend nodes.

**Recommendation: (a) for unit-level + (b) as the M0 acceptance gate.** `vite build --mode demo` passing while cards render empty is exactly what (b) catches and (a) does not.

### 2.2 Write the two guard tests that catch the silent failures

Both documented silent-failure modes are cheaply testable, and an agent *will* hit them. See `src/voicegateway/tests/test_schema_guards.py` (written alongside this doc):

- **`test_single_alembic_head`** — the chain must never fork. Head is already a two-parent merge; an agent adding a revision off the wrong parent creates a second head and nothing errors until a deploy.
- **`test_all_models_registered`** — every `models/*_model.py` class must appear in `SQLModel.metadata`. Miss a `models/__init__.py` re-export and autogen silently skips the table.

These are prerequisites, not nice-to-haves. **Add them before node 1.**

### 2.3 Resolve the four §10 decisions

Agents cannot make these. They block M1/M2/M3:

1. **`is_probe` discriminator for load traffic** — `run_id` via `X-VG-Attempt` → participant attributes (needs `headers_to_attributes` on the trunk), or a dedicated trunk id. Without it, load traffic silently pollutes production percentiles.
2. **Correlation-rate metric** — ship it from day one or you won't know the `sessions ↔ calls` join is failing.
3. **Percentile policy** — new surfaces use `compute_percentiles`; anything under 10 samples renders "max of N", not "p95".
4. **Verdict collapse version bump** — changes `voicegw livekit check` exit codes for existing users. Minor or major, plus CHANGELOG.

### 2.4 Clean the launch state

You're on `security/audit-2026-07` with 3 uncommitted files. Land or stash that, then branch per milestone (`feat/e2e-m0`, …). Confirm `branch-guard.sh` allows the new branches.

### 2.5 Set the guardrails

- **No `git push`, no PR creation** without confirmation.
- **No `alembic upgrade head`** against any real database. Migrations verified against a throwaway SQLite file only.
- **No AWS calls at all** in M0–M4. M5 is gated separately.
- Let `run-affected-tests.sh` do its job — don't have agents skip it.

## 3. The node graph

Dependencies are hard: a node may not start until all `needs` are green.

```
                     ┌─ G1 schema-guard tests ─┐   (prep, blocks everything backend)
                     │                          │
 G0 frontend-gate ───┤                          │
 (vitest/playwright) │                          │
                     ▼                          ▼
        V1 ──> V2 ──> V3                T1 ──> T2 ──> T4 ──> T5 ──> C1 ──> V4 ──> V5
        (M0)         (M2 ui)             │      │            ^      ^        │
                                         │      └────────────┘      │        └──> V6 (M6)
                                         │                          │
                                         ├──> R1 ───────────────────┘
                                         │
                                         └──> T3 ──> C2   (M4)

 L0 (SIPp spike, HITL) ──> L2 ──> L3
                            │
 L1 ─────────────────────────┴──> L4 ──> L5 ──> L6      (M5, all HITL-gated)
                                                  ^
 T5 ──────────────────────────────────────────────┘
```

**Alembic chain — assign all revision ids up front**, one linear chain off `06836270c254`, so out-of-order implementation still yields one head:
`rev1` calls+call_legs+sessions cols → `rev2` call_events → `rev3` diagnostics_runs → `rev4` node_samples → `rev5` answer latency → `rev6` managed_compute_targets → `rev7` load_runs.

**Critical path: T1 → T2 → T5 → C1.** Everything the product claims as new depends on those four.

## 4. Per-node autonomy and gate

| Node | M | Zone | Gate command | Autonomy |
|---|---|---|---|---|
| G0 frontend gate | prep | B→A | new tests pass | HITL to choose approach |
| G1 schema guards | prep | A | `pytest src/voicegateway/tests/test_schema_guards.py` | **auto** |
| V1 tab shell + surface payloads | M0 | B | tsc + both builds + render assertions | auto **if G0 done** |
| V2 load curve + error classes | M0 | B | same | auto if G0 |
| T1 calls + call_legs + repo | M1 | A | ruff+mypy+pytest, G1 | **auto** |
| T2 webhook receiver | M1 | A | same + signature-verification test | **auto** |
| R1 persist diagnostics runs | M1 | A | same | **auto** |
| T4 `/v1/calls/observations` | M2 | A | same + bounded-queue/drop-counter test | **auto** |
| T5 answer latency + source | M2 | A | same + precedence unit tests | **auto** |
| C1 correlation join | M2 | A | same + correlation-rate test | **auto** |
| V3 layer waterfall | M2 | B | frontend gate | auto if G0 |
| V4 gates + exit code | M3 | A+B | pytest + CLI exit-code test | **auto** (version bump = HITL) |
| V5 report export | M3 | B | build + fixture snapshot | auto if G0 |
| T3 prometheus scrape | M4 | A | pytest against a canned exposition fixture | **auto** |
| C2 node correlation | M4 | A | pytest | **auto** |
| V6 exposition endpoint | M6 | A | pytest | **auto** |
| L0 SIPp spike | gate | C | real 200 OK + two-way audio **over TLS/SRTP** | **HITL** |
| L1–L6 | M5 | C | real Fargate tasks, real money | **HITL each** |

## 5. Recommended execution pattern

**Not one long autonomous run.** Milestone-scoped autonomy with a human gate between milestones:

1. **Per node**: a subagent implements + runs its gate + reports. Nodes with no dependency between them run in parallel.
2. **Per milestone**: agents run unattended until every node is green, then **stop for review**. Review the diff, not the process.
3. **Between milestones**: you decide whether to continue. Drift accumulates across days; a review gate every few nodes is what keeps it honest.

**Where `/loop` fits:** it's good for *"keep working the graph until the next HITL gate"* — a self-paced driver that picks the next ready node, dispatches it, checks the gate, and stops at a boundary. It's the wrong tool for *"build all of M0–M6 unattended"*, because L0 and M5 need a human and the §10 decisions need judgment.

**Realistic split:** M0 through M4 is genuinely autonomous once G0 and G1 exist — that's the majority of the build. L0 is a one-off human spike. M5 stays supervised because it spends money in a real AWS account.

## 6. The three ways this goes wrong

1. **Frontend nodes ship green and render nothing.** Compile-passes is not renders-correctly, and the existing demo fixtures actively mislead. → G0 is mandatory, not optional.
2. **A migration forks the alembic chain** or a model misses its `__init__` re-export. Both fail silently, both surface at deploy. → G1 is mandatory.
3. **Scope drift over a multi-day autonomous build.** The spec's "not being built" table is the guardrail — put it in the agent's context on every node, or agents will helpfully add the thing you deliberately cut.
