import { writeFileSync } from "node:fs"
import { pages } from "./pages.ts"

export const slug = (title: string) => title.toLowerCase().replace(/[^a-z0-9]+/g, "-")

/** Global setup of playwright.screens.config.ts: writes docs/screens/screens.json from the page list. */
export default function writeCatalog() {
  const screens = pages.map((p) => ({
    title: p.title,
    route: "/" + p.path,
    group: p.group,
    source: "dashboard/" + p.source,
    images: { desktop: `${slug(p.title)}.desktop.jpg`, phone: `${slug(p.title)}.phone.jpg` },
  }))
  const catalog = {
    about: "Every VoiceGateway console page, photographed from the real code on the mock API's sample data. Regenerate with `make dashboard-screens` after any UI change.",
    data: "sample (dashboard/src/mocks), not a live install",
    screens,
  }
  writeFileSync(new URL("../../docs/screens/screens.json", import.meta.url), JSON.stringify(catalog, null, 2) + "\n")
}
