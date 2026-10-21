import { test, expect, type Page, type TestInfo } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"
import { watchErrors } from "./console-errors.ts"

// Project onboarding at phone and desktop widths, in both themes: the "No project yet" screen with
// the Add project form (an organisation the mock adds with no project, through localStorage
// "vg.mock-empty-org"), and Settings > Connection with the LiveKit project and its key change. The
// mock is a self-hosted install (the entitlement API answers 404); "vg.mock-entitlement" = "hosted"
// makes it a hosted one. Neither shows a self-hosted command. Every value pasted is made up.
const widths = [["phone", 390, 844], ["desktop", 1280, 800]] as const
const block = "LIVEKIT_URL=wss://example-project.livekit.cloud\nLIVEKIT_API_KEY=APIexample123\nLIVEKIT_API_SECRET=secretexample"

async function open(page: Page, testInfo: TestInfo, width: number, height: number, path: string, storage: Record<string, string> = {}) {
  const watched = watchErrors(page)
  await page.setViewportSize({ width, height })
  await page.addInitScript(({ theme, storage }) => {
    localStorage.setItem("vg-theme", theme)
    for (const [k, v] of Object.entries(storage)) localStorage.setItem(k, v)
  }, { theme: testInfo.project.use.colorScheme ?? "light", storage })
  await page.goto(path)
  return watched
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle")
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(async () => {
    await Promise.all(document.getAnimations().filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity).map((animation) => animation.finished.catch(() => undefined)))
  })
}

async function accessible(page: Page, testInfo: TestInfo, errors: ReturnType<typeof watchErrors>, shot: string) {
  await settle(page)
  const results = await new AxeBuilder({ page }).analyze()
  await testInfo.attach("axe", { body: JSON.stringify(results, null, 2), contentType: "application/json" })
  // A full-page capture resizes the viewport to the document's height, so with a modal open the page
  // behind it re-lays out at that size and the capture waits for it to settle: 25 s on a loaded CI
  // runner. A dialog is fixed to the viewport anyway, so capture just the viewport then.
  const dialogOpen = (await page.getByRole("dialog").count()) > 0
  await testInfo.attach(shot, { body: await page.screenshot({ fullPage: !dialogOpen, animations: "disabled" }), contentType: "image/png" })
  expect.soft(errors.unexpected(), "browser console and uncaught errors").toEqual([])
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map((node) => node.target) })), "serious/critical axe violations").toEqual([])
}

const fitsWidth = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

// A paste as the browser delivers it, with the text on the event's clipboard data.
const paste = (page: Page, label: string, text: string) =>
  page.getByLabel(label, { exact: true }).evaluate((el, text) => {
    const data = new DataTransfer()
    data.setData("text/plain", text)
    el.focus()
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }))
  }, text)

const emptyOrg = { "vg.mock-empty-org": "true" }
const hosted = { "vg.mock-entitlement": "hosted" }
const accepted = () => ({ lastAcceptedAt: new Date().toISOString(), lastRejectedAt: null, reason: null, usedKey: null, expectedKey: null })
// The Cloud setup journey's screens, each from the mock's data: an organisation with no project, a
// project with no call, its agent connected, its first call stored (a captured call).
const cloudScreens: { name: string; path: string; storage: () => Record<string, string>; heading: string | RegExp; act?: (page: Page) => Promise<void> }[] = [
  { name: "welcome", path: "/org/new-voice-team/project/none/setup", storage: () => ({ ...emptyOrg, ...hosted }), heading: "See inside every voice call." },
  {
    name: "connect",
    path: "/org/new-voice-team/project/none/setup?step=1",
    storage: () => ({ ...emptyOrg, ...hosted }),
    heading: "Bring your LiveKit project.",
    act: async (page) => {
      await page.waitForTimeout(600)
      await page.evaluate((t) => {
        const d = new DataTransfer()
        d.setData("text/plain", t)
        document.body.dispatchEvent(new ClipboardEvent("paste", { clipboardData: d, bubbles: true, cancelable: true }))
      }, block)
      await expect(page.getByText("Key verified")).toBeVisible()
      await expect(page.getByRole("button", { name: "Continue" })).toBeVisible()
      await expect(page.getByText(/rooms? live right now|^Connected$/)).toBeVisible()
      // The checks and the button rise in with script-driven motion, which settle() cannot see.
      await page.waitForTimeout(1500)
    },
  },
  { name: "agent", path: "/org/default/project/default-project/setup", storage: () => ({ ...hosted, "vg.mock-no-calls": "true" }), heading: "Add one line to your agent." },
  {
    name: "agent error",
    path: "/org/default/project/default-project/setup",
    storage: () => ({ ...hosted, "vg.mock-no-calls": "true", "vg.mock-connection": JSON.stringify({ agent: { lastAcceptedAt: null, lastRejectedAt: new Date().toISOString(), reason: "key_mismatch", usedKey: "APIother", expectedKey: "APIexample123" } }) }),
    heading: "Add one line to your agent.",
    act: async (page) => {
      await expect(page.getByText("Your agent uses a different LiveKit API key from this project's.")).toBeVisible()
      await page.getByRole("button", { name: "Details" }).click()
    },
  },
  { name: "connect again", path: "/org/default/project/default-project/setup?step=1", storage: () => ({ ...hosted, "vg.mock-no-calls": "true" }), heading: "Your LiveKit project is connected." },
  { name: "call", path: "/org/default/project/default-project/setup", storage: () => ({ ...hosted, "vg.mock-no-calls": "true", "vg.mock-connection": JSON.stringify({ agent: accepted() }) }), heading: "Now, call your agent." },
  {
    name: "first look",
    path: "/org/default/project/default-project/setup",
    storage: () => ({ ...hosted, "vg.mock-connection": JSON.stringify({ agent: accepted(), firstCall: { id: "vg-capture-1791864362", startedAt: new Date().toISOString() } }) }),
    heading: /^Your agent answered/,
  },
]

for (const [label, width, height] of widths) {
  test(`a self-hosted org with no project keeps the simple add-project form at ${label} width`, async ({ page }, testInfo) => {
    // Each width is set here, so the theme projects run it once each and the phone projects not at all.
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    const errors = await open(page, testInfo, width, height, "/org/new-voice-team/project/none/overview", emptyOrg)
    const form = page.getByRole("form", { name: "Add project" })
    await expect(form).toBeVisible({ timeout: 15_000 })
    await expect(form.getByLabel("Name", { exact: true })).toHaveValue(/^[a-z]+(-[a-z]+)+$/)
    // Onboarding shows no self-hosted command or text on any install.
    await expect(page.getByLabel("Project add command")).toHaveCount(0)
    await expect(page.getByText(/docker compose|Self-hosted\?/)).toHaveCount(0)
    await paste(page, "Paste from LiveKit", block)
    await expect(form.getByLabel("LiveKit URL")).toHaveValue("wss://example-project.livekit.cloud")
    await expect(form.getByLabel("API key")).toHaveValue("APIexample123")
    await expect(form.getByLabel("API secret")).toHaveValue("secretexample")
    await expect(form.getByLabel("Paste from LiveKit")).toHaveValue("")
    await expect(form.getByText("Filled URL, key and secret from the pasted variables.")).toBeVisible()
    expect(await fitsWidth(page)).toBe(true)
    await accessible(page, testInfo, errors, `add-project-${label}`)
  })

  test(`a Cloud org with no project opens the setup journey at ${label} width`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    const errors = await open(page, testInfo, width, height, "/org/new-voice-team/project/none/overview", { ...emptyOrg, ...hosted })
    await expect(page).toHaveURL(/\/setup$/, { timeout: 15_000 })
    await expect(page.getByRole("heading", { level: 1, name: "See inside every voice call." })).toBeVisible()
    expect(await fitsWidth(page)).toBe(true)
    await accessible(page, testInfo, errors, `s0-${label}`)
  })

  for (const s of cloudScreens) {
    test(`setup ${s.name} at ${label} width is accessible and fits`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
      const errors = await open(page, testInfo, width, height, s.path, s.storage())
      await expect(page.getByRole("heading", { level: 1, name: s.heading })).toBeVisible({ timeout: 15_000 })
      await expect(page.getByText(/docker|self-hosted|open-source|S3 bucket/i)).toHaveCount(0)
      if (s.act) await s.act(page)
      expect(await fitsWidth(page)).toBe(true)
      await accessible(page, testInfo, errors, `${s.name}-${label}`)
    })
  }

  test(`Settings stay reachable on a Cloud project in setup at ${label} width`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    const errors = await open(page, testInfo, width, height, "/org/default/project/default-project/overview?settings=connection", { ...hosted, "vg.mock-no-calls": "true" })
    await expect(page.getByRole("form", { name: "Change key and secret" })).toBeVisible({ timeout: 15_000 })
    await expect(page).not.toHaveURL(/\/setup/)
    expect(await fitsWidth(page)).toBe(true)
    await accessible(page, testInfo, errors, `settings-in-setup-${label}`)
  })

  test(`connection settings at ${label} width show the LiveKit project and change its key`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    const errors = await open(page, testInfo, width, height, "/org/default/project/default-project/overview?settings=connection")
    const form = page.getByRole("form", { name: "Change key and secret" })
    await expect(form).toBeVisible({ timeout: 15_000 })
    const dialog = page.getByRole("dialog")
    await expect(dialog.getByText("wss://example-project.livekit.cloud", { exact: true })).toBeVisible()
    await expect(dialog.getByText("APIexample123", { exact: true })).toBeVisible()
    expect(await fitsWidth(page)).toBe(true)
    await accessible(page, testInfo, errors, `connection-${label}`)
    await paste(page, "Paste from LiveKit", block.replace("APIexample123", "APInewexample456"))
    await expect(form.getByLabel("New API key")).toHaveValue("APInewexample456")
    const saved = page.waitForResponse((r) => r.url().endsWith("/livekit-credentials") && r.request().method() === "PUT")
    await form.getByRole("button", { name: "Change key and secret" }).click()
    expect((await saved).status()).toBe(204)
    await expect(dialog.getByText("APInewexample456", { exact: true })).toBeVisible()
    await expect(form.getByLabel("New API secret")).toHaveValue("")
  })
}
