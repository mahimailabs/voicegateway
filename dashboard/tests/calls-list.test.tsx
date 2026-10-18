// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, test } from "vitest"
import { http, HttpResponse, delay } from "msw"
import { createFilterQuery, createFilterRule } from "../src/components/reui/filters/filters-query"
import { totals } from "../src/mocks/calls-query"
import { calls, project, server, showCalls } from "./calls-ui"

const links = () => screen.getAllByRole("link").filter((el) => el.hasAttribute("data-call-id"))
const duration = (s: number) => s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s} s`

test("the list renders every captured call with its room, start and duration", async () => {
  await showCalls()
  await screen.findByRole("heading", { name: "Calls" })
  await waitFor(() => expect(links().length).toBeGreaterThan(0))
  while (screen.queryByRole("button", { name: /Show more/ })) {
    const before = links().length
    await userEvent.setup().click(screen.getByRole("button", { name: /Show more/ }))
    await waitFor(() => expect(links().length).toBeGreaterThan(before))
  }
  expect(links()).toHaveLength(calls.length)
  for (const call of calls) {
    const link = links().find((el) => el.getAttribute("data-call-id") === call.id)!
    expect(link.textContent).toBe(call.room)
    expect(link.getAttribute("href")).toContain(`/calls/${encodeURIComponent(call.id)}`)
    const row = within(link.closest("tr")!)
    const started = new Date(call.startedAt).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    expect(row.getAllByText(started).length).toBeGreaterThan(0)
    if (call.durationSeconds !== null) expect(row.getByText(duration(call.durationSeconds))).toBeTruthy()
  }
})

test("archiving a call moves it to Archived and it can be restored", async () => {
  await showCalls()
  await waitFor(() => expect(links().length).toBeGreaterThan(0))
  const link = links()[0]
  const id = link.getAttribute("data-call-id")
  const user = userEvent.setup()
  await user.click(within(link.closest("tr")!).getByRole("checkbox"))
  await user.click(screen.getByRole("button", { name: "Archive" }))
  await waitFor(() => expect(links().some((el) => el.getAttribute("data-call-id") === id)).toBe(false))
  await user.click(screen.getByRole("tab", { name: /Archived/ }))
  await waitFor(() => expect(links()).toHaveLength(1))
  expect(links()[0].getAttribute("data-call-id")).toBe(id)
  await user.click(within(links()[0].closest("tr")!).getByRole("checkbox"))
  await user.click(screen.getByRole("button", { name: "Move back to calls" }))
  expect(await screen.findByText("No archived calls.")).toBeTruthy()
})

test("the time range filters calls and persists in the URL", async () => {
  const router = await showCalls()
  await waitFor(() => expect(links().length).toBeGreaterThan(0))
  await userEvent.setup().click(screen.getByRole("radio", { name: "Last 15 minutes" }))
  await waitFor(() => expect(router.state.location.search).toMatchObject({ range: "15m" }))
  const latest = Math.max(...calls.map((c) => Date.parse(c.startedAt)))
  const last = calls.find((c) => Date.parse(c.startedAt) === latest)!
  const end = Date.parse(last.endedAt ?? last.startedAt)
  const expected = calls.filter((c) => Date.parse(c.startedAt) >= end - 900000 && Date.parse(c.startedAt) <= end)
  await waitFor(() => expect(links().map((el) => el.getAttribute("data-call-id")).sort()).toEqual(expected.map((c) => c.id).sort()))
})

test("an empty project says no calls yet", async () => {
  server.use(http.get(`${project}/calls`, () => HttpResponse.json({ items: [], page: { nextCursor: null, snapshot: "empty" }, totals: totals([]) })))
  await showCalls()
  expect(await screen.findByText("No calls yet")).toBeTruthy()
})

test("a failed read shows the error and Retry recovers", async () => {
  server.use(http.get(`${project}/calls`, ({ request }) => {
    if (!new URL(request.url).searchParams.has("archived")) return HttpResponse.json({ code: "internal_error", message: "Call storage unavailable", requestId: "test" }, { status: 500 })
  }))
  await showCalls()
  expect(await screen.findByText("Call storage unavailable")).toBeTruthy()
  server.resetHandlers()
  await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }))
  await waitFor(() => expect(links().length).toBeGreaterThan(0))
})

test("a pending read says loading rather than no calls", async () => {
  server.use(http.get(`${project}/calls`, async () => { await delay(150); return HttpResponse.json({ items: [], page: { nextCursor: null, snapshot: "empty" }, totals: totals([]) }) }))
  await showCalls()
  expect(await screen.findByText("Loading calls")).toBeTruthy()
  expect(screen.queryByText("No calls yet")).toBeNull()
  expect(await screen.findByText("No calls yet")).toBeTruthy()
})


test("a shared room filter limits the rows and survives opening its call", async () => {
  const call = calls[0]
  const q = createFilterQuery([createFilterRule({ id: "room-equals", path: ["room"], operator: "is", value: call.room })])
  const router = await showCalls(undefined, { q })
  await waitFor(() => expect(links()).toHaveLength(calls.filter((c) => c.room === call.room).length))
  expect(links().every((el) => el.textContent === call.room)).toBe(true)
  await userEvent.setup().click(links()[0])
  await waitFor(() => expect(router.state.location.pathname).toBe(`/calls/${call.id}`))
  expect(router.state.location.search).toMatchObject({ q })
  expect(await screen.findByRole("dialog")).toBeTruthy()
})
