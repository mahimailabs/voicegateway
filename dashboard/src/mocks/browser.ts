import { setupWorker } from "msw/browser"
import { handlers } from "./handlers"

// Starts the mock API in the browser. Requests the handlers do not know, such as the app's own
// files, go to the network untouched.
export async function startMockApi() {
  await setupWorker(...handlers).start({ onUnhandledRequest: "bypass", quiet: true })
}
