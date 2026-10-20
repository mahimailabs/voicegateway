import { test, expect } from "@playwright/test"

// Exercises the production controls through HTTP. Native tests separately prove real persistence,
// authorization and independent web instances; the explicit preview adapter supplies this fixture.
test("call tags and archive survive reload through scoped API writes", async ({ page }) => {
  const callId = "vg-capture-1791863749"
  await page.goto(`/org/default/project/default-project/calls/${callId}`)
  await page.getByRole("button", { name: "Add tag", exact: true }).click({ timeout: 15_000 })
  await page.getByPlaceholder("Find or create a tag").fill("Follow-up")
  const tagged = page.waitForResponse(r => r.url().endsWith(`/calls/${callId}/metadata`) && r.request().method() === "PUT")
  await page.getByRole("option", { name: 'Create "follow-up"' }).click()
  const tagResponse = await tagged
  expect(tagResponse.status()).toBe(200)
  expect(tagResponse.request().postDataJSON()).toEqual({ tags: ["follow-up"], archived: false, revision: 0 })
  await expect(page.getByRole("button", { name: "Remove tag follow-up" })).toBeVisible()

  const archived = page.waitForResponse(r => r.url().endsWith(`/calls/${callId}/metadata`) && r.request().method() === "PUT")
  await page.getByRole("button", { name: "Archive", exact: true }).click()
  const archiveResponse = await archived
  expect(archiveResponse.status()).toBe(200)
  expect(archiveResponse.request().postDataJSON()).toEqual({ tags: ["follow-up"], archived: true, revision: 1 })
  await expect(page.getByRole("button", { name: "Unarchive", exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole("button", { name: "Remove tag follow-up" })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole("button", { name: "Unarchive", exact: true })).toBeVisible()
})
