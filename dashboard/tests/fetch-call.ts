// What a stubbed fetch was asked for, whether the caller passed a URL string (session.ts) or a
// Request (the generated API client), with the URL as path and query only.
export async function seen(input: Request | string | URL, init?: RequestInit) {
  if (input instanceof Request) {
    const url = new URL(input.url)
    const body = input.method === "GET" || input.method === "HEAD" ? "" : await input.clone().text()
    return { method: input.method, url: url.pathname + url.search, body: body || null }
  }
  return { method: init?.method ?? "GET", url: String(input), body: (init?.body as string | undefined) ?? null }
}
