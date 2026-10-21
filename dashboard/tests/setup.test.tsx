// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, test, vi } from "vitest"
import type { Call } from "../src/lib/api.ts"
import type { ConnectionSource, ProjectConnection } from "../src/lib/api-types"
import { handlers } from "../src/mocks/handlers.ts"
import { scopeRewrite } from "../src/lib/url-state.ts"
import { firstLook, planText, projectName, roomsLine, setupStep, viewedStep } from "../src/features/setup/setup-model.ts"

// The Cloud setup journey: the step from the data, and each screen's states. Every value is made up
// and reaches no LiveKit.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
  globalThis.IntersectionObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return []
    }
  } as unknown as typeof IntersectionObserver
})
const { ConnectScreen, ConnectedScreen, WelcomeScreen } = await import("../src/features/setup/connect-screens.tsx")
const { AgentScreen, CallScreen, FirstLookScreen } = await import("../src/features/setup/run-screens.tsx")

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }))
afterEach(() => {
  cleanup()
  server.resetHandlers()
})
afterAll(() => server.close())

const project = "*/api/v1/org/:org/project/:project"
const now = Date.parse("2026-10-21T12:00:00Z")
const ago = (min: number) => new Date(now - min * 60_000).toISOString()
const none: ConnectionSource = { lastAcceptedAt: null, lastRejectedAt: null, reason: null, usedKey: null, expectedKey: null }
const connection = (over: Partial<ProjectConnection> = {}): ProjectConnection => ({
  livekit: { url: "wss://example-project.livekit.cloud", apiKey: "APIexample123" },
  urls: { observability: "https://vg.example.com/ingest/acme/example-project", webhook: "https://vg.example.com/ingest/acme/example-project/livekit/webhook" },
  webhook: none,
  agent: none,
  sdk: none,
  firstCall: null,
  ...over,
})
const block = "LIVEKIT_URL=wss://example-project.livekit.cloud\nLIVEKIT_API_KEY=APIexample123\nLIVEKIT_API_SECRET=secretexample"
// Nothing self-hosted, and nothing priced, on a Cloud screen.
const cloudClean = () => expect(document.body.textContent ?? "").not.toMatch(/docker|self-hosted|open-source|S3|bucket|\$|cost|price/i)

function show(node: () => React.ReactNode) {
  scopeRewrite.input({ url: new URL("http://localhost/org/acme/project/example-project/setup") })
  const root = createRootRoute({ component: () => <Outlet /> })
  const routes = ["/", "/calls", "/calls/$callId"].map((path) => createRoute({ getParentRoute: () => root, path, component: path === "/" ? node : () => <p>{path}</p> }))
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: ["/"] }) })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

describe("the step from the data", () => {
  test.each([
    ["no project", false, undefined, 1],
    ["a project whose connection is not read yet", true, undefined, 2],
    ["every source waiting", true, connection(), 2],
    ["only the webhook accepted", true, connection({ webhook: { ...none, lastAcceptedAt: ago(1) } }), 2],
    ["the agent refused, never accepted", true, connection({ agent: { ...none, lastRejectedAt: ago(1), reason: "signature_invalid" } }), 2],
    ["the agent upload accepted", true, connection({ agent: { ...none, lastAcceptedAt: ago(1) } }), 3],
    ["an SDK batch accepted", true, connection({ sdk: { ...none, lastAcceptedAt: ago(1) } }), 3],
    ["a first call stored", true, connection({ agent: { ...none, lastAcceptedAt: ago(1) }, firstCall: { id: "c1", startedAt: ago(2) } }), 4],
  ] as const)("%s is step %i", (_n, hasProject, c, step) => {
    expect(setupStep(hasProject, c, now)).toBe(step)
  })

  test("Welcome opens first with no project; a revisited step never runs ahead of the data", () => {
    expect(viewedStep(1, null)).toBe(0)
    expect(viewedStep(1, 1)).toBe(1)
    expect(viewedStep(3, null)).toBe(3)
    expect(viewedStep(3, "2")).toBe(2)
    expect(viewedStep(2, 4)).toBe(2)
    expect(viewedStep(2, "nonsense")).toBe(2)
  })

  test("the project is named after its LiveKit subdomain, generated only when there is none", () => {
    expect(projectName("wss://mahimai-kan8nzy4.livekit.cloud", () => "gen")).toBe("mahimai-kan8nzy4")
    expect(projectName("wss://lk.example.com", () => "gen")).toBe("lk")
    for (const url of ["ws://127.0.0.1:7880", "ws://localhost:7880", "wss://example.com", "not a url"]) expect(projectName(url, () => "gen")).toBe("gen")
  })

  test("the room count: unknown is never zero", () => {
    expect(roomsLine(null)).toBe("Connected")
    expect(roomsLine(0)).toBe("No rooms live right now")
    expect(roomsLine(1)).toBe("1 room live right now")
    expect(roomsLine(12)).toBe("12 rooms live right now")
    expect(planText(undefined)).toBeNull()
  })
})

const turn = (over: Partial<NonNullable<Call["turns"]>[number]>) => ({ userText: "hi", agentText: "hello", model: "example-llm", llmMs: null, ttftMs: null, sttMs: null, ttsMs: null, endOfTurnMs: null, inputTokens: null, outputTokens: null, audioSeconds: null, characters: null, clockId: "c", ...over })
const callWith = (turns: ReturnType<typeof turn>[]): Call => ({
  id: "call-1", room: "first-call", roomSid: null, startedAt: ago(5), endedAt: ago(4), durationSeconds: 60, participantCount: 2, endReason: null, spans: null, turns,
  recording: { url: "/r", duration: 60, segments: [], peaks: null, id: "r1", clockId: "c" }, endState: "observed", tags: [], archived: false, clocks: [],
})

describe("firstLook", () => {
  test("the first measured turn and the slowest of its known stages", () => {
    const f = firstLook(callWith([turn({}), turn({ e2eMs: 2400, ttftMs: 1200, endOfTurnMs: 600 })]))
    expect(f.answerMs).toBe(2400)
    expect(f.turn).toBe(2)
    expect(f.stages.map((s) => s.name)).toEqual(["end of turn detection", "the LLM's first token"])
    expect(f.slowest).toEqual({ name: "the LLM's first token", ms: 1200 })
    expect(f.turns).toBe(2)
  })
  test("no stage times: the slowest stage is unknown", () => {
    expect(firstLook(callWith([turn({ e2eMs: 900 })])).slowest).toBeNull()
  })
  test("no measured turn: the answer time is unknown", () => {
    const f = firstLook(callWith([turn({})]))
    expect(f.answerMs).toBeNull()
    expect(f.turn).toBeNull()
  })
})

describe("Welcome", () => {
  test("one headline, an example turn labelled as one, one button", async () => {
    const start = vi.fn()
    show(() => <WelcomeScreen onStart={start} />)
    expect(await screen.findByRole("heading", { level: 1, name: "See inside every voice call." })).toBeTruthy()
    expect(screen.getByText("Example call, one turn")).toBeTruthy()
    await userEvent.setup().click(screen.getByRole("button", { name: "Connect LiveKit" }))
    expect(start).toHaveBeenCalled()
    cloudClean()
  })
})

describe("Connect", () => {
  const record = () => {
    const bodies: Record<string, unknown>[] = []
    return {
      bodies,
      answer: (status: number, body: unknown) =>
        server.use(
          http.post("*/api/v1/org/:org/projects", async ({ request }) => {
            bodies.push((await request.json()) as Record<string, unknown>)
            return HttpResponse.json(body as never, { status })
          }),
        ),
    }
  }

  test("a paste on the target sends the keys named after the subdomain, then shows the checks and the room count", async () => {
    const { bodies, answer } = record()
    answer(201, { id: "example-project", name: "example-project", liveRooms: 3 })
    const onContinue = vi.fn()
    show(() => <ConnectScreen orgId="acme" onContinue={onContinue} />)
    const user = userEvent.setup()
    await user.click(await screen.findByLabelText("Paste your LiveKit keys"))
    await user.paste(block)
    expect(await screen.findByText("3 rooms live right now")).toBeTruthy()
    expect(bodies[0]).toEqual({ name: "example-project", livekitUrl: "wss://example-project.livekit.cloud", livekitApiKey: "APIexample123", livekitApiSecret: "secretexample" })
    expect(screen.getByText("Key verified")).toBeTruthy()
    expect(document.body.textContent).not.toContain("secretexample")
    expect(document.body.textContent).not.toContain("APIexample123")
    await user.click(screen.getByRole("button", { name: "Continue" }))
    expect(onContinue).toHaveBeenCalledWith({ id: "example-project", name: "example-project", liveRooms: 3 })
    cloudClean()
  })

  test("a room count the verify did not take says Connected, never zero", async () => {
    record().answer(201, { id: "example-project", name: "example-project", liveRooms: null })
    show(() => <ConnectScreen orgId="acme" onContinue={vi.fn()} />)
    const user = userEvent.setup()
    await user.click(await screen.findByLabelText("Paste your LiveKit keys"))
    await user.paste(block)
    expect(await screen.findByText("Connected")).toBeTruthy()
    expect(screen.queryByText(/0 rooms/)).toBeNull()
  })

  test("a taken name is retried once with a short suffix", async () => {
    const bodies: Record<string, unknown>[] = []
    server.use(
      http.post("*/api/v1/org/:org/projects", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>
        bodies.push(body)
        return bodies.length === 1 ? HttpResponse.json({ code: "project_exists", message: "taken", requestId: "r" }, { status: 409 }) : HttpResponse.json({ id: String(body.name), name: String(body.name), liveRooms: 0 }, { status: 201 })
      }),
    )
    show(() => <ConnectScreen orgId="acme" onContinue={vi.fn()} />)
    const user = userEvent.setup()
    await user.click(await screen.findByLabelText("Paste your LiveKit keys"))
    await user.paste(block)
    expect(await screen.findByText("No rooms live right now")).toBeTruthy()
    expect(bodies).toHaveLength(2)
    expect(String(bodies[1].name)).toMatch(/^example-project-[a-z0-9]{1,4}$/)
  })

  test.each([
    ["livekit_secret_invalid", "LiveKit did not accept this key and secret."],
    ["livekit_unreachable", "LiveKit did not answer at this URL."],
  ])("422 %s shows one line and its fix, and no Continue", async (code, line) => {
    record().answer(422, { code, message: "x", requestId: "r" })
    show(() => <ConnectScreen orgId="acme" onContinue={vi.fn()} />)
    const user = userEvent.setup()
    await user.click(await screen.findByLabelText("Paste your LiveKit keys"))
    await user.paste(block)
    expect((await screen.findByRole("alert")).textContent).toContain(line)
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull()
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy()
  })

  test("the keys can be entered by hand; no name field and no bucket toggle", async () => {
    const { bodies, answer } = record()
    answer(201, { id: "example-project", name: "example-project", liveRooms: 1 })
    show(() => <ConnectScreen orgId="acme" onContinue={vi.fn()} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Enter them yourself" }))
    expect(screen.queryByLabelText(/name/i)).toBeNull()
    await user.type(screen.getByLabelText("LiveKit URL"), "wss://example-project.livekit.cloud")
    await user.type(screen.getByLabelText("API key"), "APIexample123")
    await user.type(screen.getByLabelText("API secret"), "secretexample")
    await user.click(screen.getByRole("button", { name: "Connect" }))
    expect(await screen.findByText("1 room live right now")).toBeTruthy()
    expect(bodies[0].name).toBe("example-project")
    cloudClean()
  })
})

describe("Agent", () => {
  test("waiting: a .env block and a separate Python or Node block, each with its version; the webhook folded away", async () => {
    show(() => <AgentScreen connection={connection()} now={now} onContinue={vi.fn()} />)
    const env = await screen.findByLabelText(".env")
    expect(env.textContent).toBe("LIVEKIT_OBSERVABILITY_URL=https://vg.example.com/ingest/acme/example-project")
    // Long lines scroll inside the block; nothing breaks the URL mid-token.
    expect(env.className).toContain("overflow-x-auto")
    expect(env.className).not.toContain("break-all")
    expect(screen.getByLabelText("Python session start").textContent).toBe("await session.start(room=ctx.room, agent=agent, record=True)")
    expect(screen.getByText("livekit-agents 1.8.6 or later.")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Copy .env" })).toBeTruthy()
    const user = userEvent.setup()
    await user.click(screen.getByRole("radio", { name: "Node" }))
    expect(screen.getByLabelText("Node session start").textContent).toContain("record: true")
    expect(screen.getByText("@livekit/agents 1.8.0 or later.")).toBeTruthy()
    expect(screen.getByRole("status").textContent).toContain("Waiting for your agent")
    expect(screen.queryByLabelText("Webhook URL")).toBeNull()
    await user.click(screen.getByRole("button", { name: "Show me how" }))
    expect(screen.getByLabelText("Webhook URL").textContent).toBe("https://vg.example.com/ingest/acme/example-project/livekit/webhook")
    cloudClean()
  })

  test("connected: says so and moves on by itself", async () => {
    const onContinue = vi.fn()
    show(() => <AgentScreen connection={connection({ agent: { ...none, lastAcceptedAt: ago(1) } })} now={now} onContinue={onContinue} />)
    expect((await screen.findByRole("status")).textContent).toContain("Your agent is connected")
    await waitFor(() => expect(onContinue).toHaveBeenCalled(), { timeout: 3000 })
  })

  test("an agent in Error: one line, the fix under Details", async () => {
    show(() => <AgentScreen connection={connection({ agent: { ...none, lastRejectedAt: ago(1), reason: "signature_invalid" } })} now={now} onContinue={vi.fn()} />)
    const status = await screen.findByRole("status")
    expect(within(status).getByText("The token did not verify")).toBeTruthy()
    const details = within(status).getByRole("button", { name: "Details" })
    // The link on the grey row uses the foreground token: primary blue on grey fails contrast.
    expect(details.className).toContain("text-foreground")
    await userEvent.setup().click(details)
    expect(status.textContent).toContain("Set LIVEKIT_API_SECRET in the agent to this project's LiveKit secret.")
  })

  test("a key from another LiveKit project is said in plain words", async () => {
    show(() => <AgentScreen connection={connection({ agent: { ...none, lastRejectedAt: ago(1), reason: "key_mismatch", usedKey: "APIother", expectedKey: "APIexample123" } })} now={now} onContinue={vi.fn()} />)
    const status = await screen.findByRole("status")
    expect(within(status).getByText("Your agent uses a different LiveKit API key from this project's.")).toBeTruthy()
    expect(status.textContent).not.toMatch(/token named/i)
  })
})

describe("Call", () => {
  const empty = { items: [], page: { nextCursor: null, snapshot: "s" }, totals: { callCount: 0 } }
  test("waiting: the Agents Playground, no live state", async () => {
    server.use(http.get(`${project}/calls`, () => HttpResponse.json(empty)))
    show(() => <CallScreen />)
    expect(await screen.findByRole("heading", { level: 1, name: "Now, call your agent." })).toBeTruthy()
    expect(screen.getByRole("link", { name: /Open LiveKit Agents Playground/ }).getAttribute("href")).toBe("https://agents-playground.livekit.io")
    expect(screen.queryByLabelText("Agent state")).toBeNull()
    cloudClean()
  })

  test("a live call with no turn text: the chips follow the live state and no bubbles show", async () => {
    const summary = { id: "live-2", room: "first-call", roomSid: null, startedAt: ago(1), endedAt: null, durationSeconds: null, participantCount: 2, endState: "open", channel: "web", pipeline: "cascaded", models: [], agentKey: null, turnCount: null, answerMs: null, answerMsMedian: null, issues: { high: null, medium: null, titles: [] }, tags: [], archived: false, recorded: false }
    server.use(
      http.get(`${project}/calls`, () => HttpResponse.json({ ...empty, items: [summary] })),
      http.get(`${project}/calls/live-2`, () => HttpResponse.json({ ...callWith([]), turns: null, id: "live-2", endState: "open", endedAt: null, live: { user: null, agent: { state: "thinking", at: ago(0.2) } } })),
    )
    show(() => <CallScreen />)
    const chips = await screen.findByLabelText("Agent state")
    await waitFor(() => expect(within(chips).getByText("thinking").className).toContain("bg-primary"))
    expect(screen.queryByLabelText("Transcript")).toBeNull()
  })

  test("a live call: We can hear it, the agent's state and the turn text getCall carries", async () => {
    const summary = { id: "live-1", room: "first-call", roomSid: null, startedAt: ago(1), endedAt: null, durationSeconds: null, participantCount: 2, endState: "open", channel: "web", pipeline: "cascaded", models: [], agentKey: null, turnCount: null, answerMs: null, answerMsMedian: null, issues: { high: null, medium: null, titles: [] }, tags: [], archived: false, recorded: false }
    server.use(
      http.get(`${project}/calls`, () => HttpResponse.json({ ...empty, items: [summary] })),
      http.get(`${project}/calls/live-1`, () => HttpResponse.json({ ...callWith([turn({ userText: "Do you open on Sunday?", agentText: "Hello, how can I help?" })]), id: "live-1", endState: "open", endedAt: null, live: { user: { state: "listening", at: ago(0.5) }, agent: { state: "speaking", at: ago(0.2) } } })),
    )
    show(() => <CallScreen />)
    expect(await screen.findByRole("heading", { level: 1, name: "We can hear it." })).toBeTruthy()
    const chips = await screen.findByLabelText("Agent state")
    await waitFor(() => expect(within(chips).getByText("speaking").className).toContain("bg-primary"))
    expect(within(chips).getByText("thinking").className).not.toContain("bg-primary")
    expect(await screen.findByText("Hello, how can I help?")).toBeTruthy()
    expect(screen.getByText("Do you open on Sunday?")).toBeTruthy()
  })
})

describe("First look", () => {
  const serve = (call: Call, hosted = true) =>
    server.use(
      http.get(`${project}/calls/call-1`, () => HttpResponse.json(call)),
      http.get("*/api/v1/org/:org/entitlement", () =>
        hosted ? HttpResponse.json({ from: ago(1000), to: ago(-1000), limitMinutes: 2000, active: true, revision: 1, callSeconds: 150, unmeasuredCalls: 0, openCalls: 0, warningPercent: 0, ingestAllowed: true }) : HttpResponse.json({ code: "not_found", message: "x", requestId: "r" }, { status: 404 }),
      ),
    )

  test("the answer time, the slowest stage, turns, interruptions and the plan; no cost", async () => {
    serve(callWith([turn({ e2eMs: 2400, ttftMs: 1200, endOfTurnMs: 600, ttsMs: 300 }), turn({ e2eMs: 900 })]))
    show(() => <FirstLookScreen callId="call-1" />)
    expect(await screen.findByText("Your first call is in.")).toBeTruthy()
    // 1.20 s of 2.40 s is half, not most.
    expect(screen.getByText("The biggest part was the LLM's first token: 1.20 s of 2.40 s.")).toBeTruthy()
    expect(screen.getByRole("figure", { name: "Turn 1" })).toBeTruthy()
    expect(await screen.findByText("2 of 2,000 min")).toBeTruthy()
    expect(screen.getByRole("link", { name: "Open this call" }).getAttribute("href")).toBe("/calls/call-1")
    expect(screen.getByRole("link", { name: "Go to all calls" }).getAttribute("href")).toBe("/calls")
    cloudClean()
  })

  test("a stage over half the answer is most of the wait", async () => {
    serve(callWith([turn({ e2eMs: 2000, ttftMs: 1500, endOfTurnMs: 300 })]))
    show(() => <FirstLookScreen callId="call-1" />)
    expect(await screen.findByText("Most of the wait was the LLM's first token: 1.50 s of 2.00 s.")).toBeTruthy()
  })

  test("unknown stays unknown: no measured turn, no plan read", async () => {
    serve(callWith([turn({})]), false)
    show(() => <FirstLookScreen callId="call-1" />)
    expect(await screen.findByRole("heading", { level: 1, name: "Your agent answered." })).toBeTruthy()
    expect(screen.getByText("No turn measured its answer time, so the breakdown is unknown.")).toBeTruthy()
    expect(screen.queryByRole("figure")).toBeNull()
    await waitFor(() => expect(within(screen.getByText("Your plan").parentElement!).getByText("Unknown")).toBeTruthy())
  })
})

describe("rail", () => {
  test("dots after the step on screen are grey, even when the data has reached them", async () => {
    const { Rail } = await import("../src/features/setup/setup-page")
    const { container } = render(<Rail derived={3} viewed={2} onView={() => {}} />)
    const dots = Array.from(container.querySelectorAll("li > * > span[aria-hidden='true']"))
    expect(dots.map((d) => d.className.includes("bg-primary"))).toEqual([true, true, false, false])
    expect(screen.getByRole("button", { name: /Call/ })).toBeTruthy()
  })
})

describe("Connect on a touch screen", () => {
  const coarse = () =>
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({ matches: query === "(pointer: coarse)", media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList)
  afterEach(() => vi.restoreAllMocks())

  test("a Paste button reads the clipboard and sends the keys; no keyboard hint", async () => {
    coarse()
    const read = vi.fn(async () => block)
    const user = userEvent.setup()
    // After userEvent.setup(), which installs its own clipboard.
    Object.defineProperty(navigator, "clipboard", { value: { readText: read }, configurable: true })
    server.use(http.post("*/api/v1/org/:org/projects", () => HttpResponse.json({ id: "example-project", name: "example-project", liveRooms: 2 }, { status: 201 })))
    show(() => <ConnectScreen orgId="acme" onContinue={vi.fn()} />)
    const paste = await screen.findByRole("button", { name: "Paste" })
    expect(screen.queryByText("⌘")).toBeNull()
    expect(screen.queryByText("Ctrl")).toBeNull()
    await user.click(paste)
    expect(read).toHaveBeenCalled()
    expect(await screen.findByText("2 rooms live right now")).toBeTruthy()
  })

  test("a refused clipboard says so in one line and opens the three fields", async () => {
    coarse()
    const user = userEvent.setup()
    Object.defineProperty(navigator, "clipboard", { value: { readText: () => Promise.reject(new DOMException("denied", "NotAllowedError")) }, configurable: true })
    show(() => <ConnectScreen orgId="acme" onContinue={vi.fn()} />)
    await user.click(await screen.findByRole("button", { name: "Paste" }))
    expect(await screen.findByText("Paste was not allowed here. Enter the keys yourself below.")).toBeTruthy()
    expect(screen.getByLabelText("LiveKit URL")).toBeTruthy()
  })
})

describe("Connect, seen again on a project that exists", () => {
  test("names the LiveKit project, links Change key to Settings > Connection, and continues", async () => {
    const onContinue = vi.fn()
    show(() => <ConnectedScreen livekit={{ url: "wss://example-project.livekit.cloud", apiKey: "APIexample123" }} onContinue={onContinue} />)
    expect(await screen.findByRole("heading", { level: 1, name: "Your LiveKit project is connected." })).toBeTruthy()
    expect(screen.getByText("example-project")).toBeTruthy()
    expect(screen.getByRole("link", { name: "Change key" }).getAttribute("href")).toBe("/overview?settings=connection")
    await userEvent.setup().click(screen.getByRole("button", { name: "Continue" }))
    expect(onContinue).toHaveBeenCalled()
  })
})
