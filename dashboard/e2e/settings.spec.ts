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


test("settings project picker changes the scope used by reads and writes", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("vg.mock-projects", JSON.stringify([{ orgId: "default", id: "second-project", name: "Second project" }])))
  await page.goto("/org/default/project/default-project/overview?settings=thresholds")
  await expect(page.getByRole("spinbutton", { name: "Answer time" })).toBeVisible({ timeout: 15_000 })
  await page.getByRole("combobox", { name: "Project", exact: true }).filter({ visible: true }).click()
  await page.getByRole("option", { name: "Second project", exact: true }).click()
  await expect(page).toHaveURL(/\/project\/second-project\/overview\?settings=thresholds/)
  const answer = page.getByRole("spinbutton", { name: "Answer time" })
  await expect(answer).toHaveValue("1.5")
  await answer.fill("3")
  const saved = page.waitForResponse((r) => r.url().endsWith("/project/second-project/settings") && r.request().method() === "PUT")
  await page.getByRole("button", { name: "Save changes", exact: true }).click()
  expect((await saved).status()).toBe(200)
  await page.getByRole("combobox", { name: "Project", exact: true }).filter({ visible: true }).click()
  await page.getByRole("option", { name: "Default project", exact: true }).click()
  await expect(page).toHaveURL(/\/project\/default-project\/overview\?settings=thresholds/)
  await expect(answer).toHaveValue("1.5")
})
