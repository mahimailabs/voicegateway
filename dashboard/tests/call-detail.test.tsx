// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, test } from "vitest"
import { captured, showCalls } from "./calls-ui"

const formatMs = (ms: number) => ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`

test("a captured call opens by id with its metadata, transcript and each turn's measured response", async () => {
  const router = await showCalls(captured.id)
  const dialog = within(await screen.findByRole("dialog"))
  expect(await dialog.findByText(captured.room, { selector: "h2" })).toBeTruthy()
  expect(dialog.getByText(`${captured.participantCount} participants`)).toBeTruthy()
  expect(dialog.getByText(new Date(captured.startedAt).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" }))).toBeTruthy()
  for (const [index, turn] of captured.turns!.entries()) {
    if (turn.userText) expect(dialog.getByText(turn.userText, { exact: true })).toBeTruthy()
    expect(dialog.getByText(turn.agentText!, { exact: true })).toBeTruthy()
    expect(dialog.getAllByLabelText(`Turn ${index + 1}`).length).toBeGreaterThan(0)
    if (turn.e2eMs != null) expect(dialog.getAllByText(formatMs(turn.e2eMs)).length).toBeGreaterThan(0)
  }
  const user = userEvent.setup()
  await user.click(dialog.getByRole("button", { name: new RegExp(captured.turns![0].agentText!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }))
  await waitFor(() => expect(router.state.location.search).toMatchObject({ turn: "1-agent" }))
  await user.click(dialog.getByRole("button", { name: /^Model call/ }))
  const field = dialog.getByText("Tokens in / out").parentElement!
  const request = captured.spans!.find((s) => s.name === "llm_request")!
  expect(within(field).getByText(`${request.attrs!["gen_ai.usage.input_tokens"]} / ${request.attrs!["gen_ai.usage.output_tokens"]}`)).toBeTruthy()
})

test("an unknown call id renders a not-found drawer", async () => {
  await showCalls("missing-call-324")
  expect(await screen.findByRole("heading", { name: "Call not found" })).toBeTruthy()
  expect(screen.getAllByText("No call missing-call-324 in this project.").length).toBeGreaterThan(0)
})
