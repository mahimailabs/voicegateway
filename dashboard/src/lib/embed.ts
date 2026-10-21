import { client } from "@/lib/api-types/client.gen"

// An embedded widget (decision 0034) reads with the embed token in its frame's address, never with
// a session: on another site the console's cookie is not sent, and it must not be. The token is read
// once at start and sent as a Bearer on every API call, so links inside the widgets carry it on.
const embedPath = /^\/org\/[^/]+\/project\/[^/]+\/embed\/[^/]+\/?$/

export const isEmbedPath = (pathname = window.location.pathname) => embedPath.test(pathname)

let token: string | null = null

// The token in the address of an embed page, or null for the console's own preview, which reads
// with the signed-in session.
export function startEmbed(): string | null {
  token = isEmbedPath() ? new URLSearchParams(window.location.search).get("token") : null
  if (token)
    client.interceptors.request.use((request) => {
      request.headers.set("Authorization", `Bearer ${token}`)
      return request
    })
  return token
}

export const embedToken = () => token

// A recording plays through an <audio> element, which cannot send the Bearer: with a token the
// audio is read with fetch and played from memory.
export async function playableUrl(url: string): Promise<string> {
  if (!token) return url
  const answer = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
  if (!answer.ok) throw new Error(`The recording could not be read (${answer.status}).`)
  return URL.createObjectURL(await answer.blob())
}
