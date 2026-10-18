// @vitest-environment jsdom
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, test } from "vitest"
import { captured, project, server, showCalls } from "./calls-ui"

test.each([null, "0"])("model usage %s stays distinct from missing usage in the drawer", async (tokens) => {
  // Only change usage on a real capture. The same UI must distinguish missing from reported zero.
  const call = structuredClone(captured)
  for (const span of call.spans ?? []) {
    if (span.name !== "llm_request") continue
    for (const key of ["gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"]) {
      if (tokens === null) delete span.attrs![key]
      else span.attrs![key] = tokens
    }
  }
  server.use(http.get(`${project}/calls/:callId`, () => HttpResponse.json(call)))
  await showCalls(call.id)
  const dialog = within(await screen.findByRole("dialog"))
  const text = await dialog.findByText(call.turns![0].agentText!, { exact: true })
  const user = userEvent.setup()
  await user.click(text.closest("button")!)
  await user.click(dialog.getByRole("button", { name: /^Model call/ }))
  const field = within(dialog.getByText("Tokens in / out").parentElement!)
  expect(field.getByText(tokens === null ? "Unknown" : "0 / 0")).toBeTruthy()
  expect(field.queryByText(tokens === null ? "0 / 0" : "Unknown")).toBeNull()
})
