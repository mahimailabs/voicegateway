import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./node_modules/.cache/playwright/results",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 2,
  reporter: [["list"], ["json", { outputFile: "node_modules/.cache/playwright/results.json" }], ["html", { outputFolder: "node_modules/.cache/playwright/report", open: "never" }]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:5198",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: ["light", "dark"].map((theme) => ({ name: theme, use: { colorScheme: theme as "light" | "dark" } })),
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 5198 --strictPort",
    url: "http://127.0.0.1:5198",
    reuseExistingServer: false,
  },
})
