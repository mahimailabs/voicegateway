import { bypass } from "msw"

// Recording audio, served the way getRecordingMedia will serve it. The captures name each recording
// by a local file under /captures (dashboard/public/captures, kept out of git); its name, the room
// SID, is the opaque id the contract asks for, and the playback url points at the API, never at the
// file. A capture whose file is not on this machine has no retained recording: 404.

export const recordingId = (assetUrl: string) => assetUrl.replace(/^.*\//, "").replace(/\.[^.]+$/, "")

const asset = (id: string) => (/^[A-Za-z0-9_-]+$/.test(id) ? `/captures/${id}.ogg` : null)

// The playback url a read returns for a recording, inside the project that was asked for.
export const playbackUrl = (orgId: string, projectId: string, id: string) => `/api/v1/org/${encodeURIComponent(orgId)}/project/${encodeURIComponent(projectId)}/recordings/${encodeURIComponent(id)}`

// The file's bytes, a byte range of them when the player asks for one. Anything but audio (the dev
// server answers a missing file with the app's page) is no recording. known says whether a capture
// names this recording, so an id alone never reaches a file.
export async function recordingMedia(id: string, request: Request, known: boolean): Promise<Response | null> {
  const path = known ? asset(id) : null
  if (!path) return null
  const headers = new Headers()
  const range = request.headers.get("Range")
  if (range) headers.set("Range", range)
  let res: Response
  try {
    res = await fetch(bypass(new Request(new URL(path, request.url), { headers })))
  } catch {
    return null
  }
  if ((res.status !== 200 && res.status !== 206) || !res.headers.get("Content-Type")?.startsWith("audio/")) return null
  const out = new Headers({ "Content-Type": "audio/ogg", "Accept-Ranges": "bytes", "Cache-Control": "private, no-store" })
  for (const h of ["Content-Length", "Content-Range"]) {
    const v = res.headers.get(h)
    if (v) out.set(h, v)
  }
  return new Response(res.body, { status: res.status, headers: out })
}
