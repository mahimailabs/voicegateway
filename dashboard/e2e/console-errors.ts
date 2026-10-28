import type { Page } from "@playwright/test"

// Browser console errors and uncaught exceptions on a page. A self-hosted install answers the
// entitlement read 404 by contract (the console asks it to tell a Cloud project from a self-hosted
// one), and the browser logs every 404 it loads. Exactly those, one logged 404 per entitlement 404
// the page received, are not errors; every other message is.
const LOGGED_404 = "Failed to load resource: the server responded with a status of 404 (Not Found)"

export function watchErrors(page: Page) {
  const errors: string[] = []
  let expected = 0
  page.on("pageerror", (error) => errors.push(error.message))
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text())
  })
  page.on("response", (response) => {
    if (response.status() === 404 && new URL(response.url()).pathname.endsWith("/entitlement")) expected++
  })
  return {
    errors,
    unexpected: () => {
      let left = expected
      return errors.filter((e) => {
        if (e === LOGGED_404 && left > 0) {
          left--
          return false
        }
        return true
      })
    },
  }
}
