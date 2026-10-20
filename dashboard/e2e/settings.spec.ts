import { test, expect } from "@playwright/test"

// The preview exercises the real client and controls. Native API tests separately
// verify Postgres persistence and authorization rather than treating preview state as storage.
test("project thresholds save and Undo use revisioned API writes", async ({ page }) => {
  await page.goto("/org/default/project/default-project/overview?settings=thresholds")
  const answer = page.getByRole("spinbutton", { name: "Answer time" })
  await expect(answer).toBeVisible({ timeout: 15_000 })
  await answer.fill("2.5")
  const saved = page.waitForResponse((r) => r.url().endsWith("/settings") && r.request().method() === "PUT")
  await page.getByRole("button", { name: "Save changes", exact: true }).click()
  const response = await saved
  expect(response.status()).toBe(200)
  expect(response.request().postDataJSON()).toEqual({ thresholds: { answerMs: 2500, joinMs: 2000, ttsStartMs: 1000 }, revision: 0 })
  expect((await response.json()).revision).toBe(1)
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled()
  const undone = page.waitForResponse((r) => r.url().endsWith("/settings") && r.request().method() === "PUT")
  await page.getByRole("button", { name: "Undo", exact: true }).click()
  const undoResponse = await undone
  expect(undoResponse.status()).toBe(200)
  expect(undoResponse.request().postDataJSON()).toEqual({ thresholds: { answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 }, revision: 1 })
  await expect(answer).toHaveValue("1.5")
})
