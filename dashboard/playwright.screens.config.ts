import { defineConfig } from "@playwright/test"

// The screen catalog (e2e/screens.spec.ts): light pictures at desktop and phone width are committed in
// docs/screens/; SCREENS_DARK=1 also takes dark ones, which stay local (ignored).
const sizes = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } }
const dark = process.env.SCREENS_DARK === "1"

export default defineConfig({
  testDir: "./e2e",
  testMatch: "screens.spec.ts",
  globalSetup: "./e2e/screens-catalog.ts",
  outputDir: "./node_modules/.cache/playwright/screens",
  fullyParallel: true,
  retries: 0,
  workers: 2,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:5199" },
  projects: Object.entries(sizes).flatMap(([name, viewport]) => [
    { name, use: { viewport, colorScheme: "light" as const } },
    ...(dark ? [{ name: name + ".dark", use: { viewport, colorScheme: "dark" as const } }] : []),
  ]),
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 5199 --strictPort",
    url: "http://127.0.0.1:5199",
    reuseExistingServer: false,
  },
})
