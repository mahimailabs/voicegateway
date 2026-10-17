import { JOIN_MS } from "@/lib/thresholds"
import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Brain, Desktop, Globe } from "@phosphor-icons/react"
import { useNavigate } from "@tanstack/react-router"
import { ProviderLogo, providerOfModel } from "@/components/provider-logo"
import { type Call, mediaServerQuery } from "@/lib/api"
import { type Hop, type Link, PathDiagram, PathLegend, hopIcons } from "../sip/call-path"
import { type MediaServerData, type Room, RoomDrawer, fromContract, regionCity } from "../media-server/room-drawer"
import { TEST_AGENTS } from "../agents/agents-model"
import { type CallModel, pipelineLabel } from "./call-model"
import { useEmbedded } from "@/lib/url-state"

// The call from the line it came in on to the models that answered it, built from what the
// infrastructure pages already read: the SIP trunk, the room, the agent's dispatch and job, and the
// model spans. A hop we did not watch says so; it never shows a zero. Room and Agent open the room
// drawer, Phone opens the SIP page, Models opens the trace.

const secs = (ms: number) => `${(ms / 1000).toFixed(1)} s`
const media: Record<string, string> = { SIP_MEDIA_ENCRYPT_DISABLE: "RTP", SIP_MEDIA_ENCRYPT_ALLOW: "SRTP if offered", SIP_MEDIA_ENCRYPT_REQUIRE: "SRTP" }
const unknown: Link = { label: "not reported", secure: null, note: "We did not watch this room, so how this hop was carried is unknown." }

// Which room the call ran in comes from the media server read; until it answers the path is not drawn,
// so a room not yet read is never shown as not watched.
export function CallPathStrip(props: { call: Call; model: CallModel; onModels: (() => void) | null }) {
  const read = useQuery(mediaServerQuery())
  const ms = useMemo(() => (read.data ? fromContract(read.data) : null), [read.data])
  if (!ms) return <p className="px-1 py-2 text-[12px] text-muted-foreground">{read.isError ? "The call path could not be loaded: the media server read failed." : "Loading the call path."}</p>
  return <PathStrip {...props} data={ms} />
}

function PathStrip({ call, model, onModels, data }: { call: Call; model: CallModel; onModels: (() => void) | null; data: MediaServerData }) {
  const navigate = useNavigate()
  const embedded = useEmbedded()
  const [roomOpen, setRoomOpen] = useState(false)
  const k = data.rooms.findIndex((r) => r.sid === call.roomSid)
  const room: Room | null = k >= 0 ? { ...data.rooms[k], n: k + 1 } : null
  const sip = room?.participants.find((p) => p.kind === "sip")
  const isConsole = !room && call.room.startsWith("console")

  // Models: STT, LLM and TTS as the spans name them, otherwise whatever models the turns reported.
  const modelOf = (name: string) => (call.spans ?? []).find((s) => s.name === name && s.attrs?.["gen_ai.request.model"])?.attrs?.["gen_ai.request.model"]
  const stack = ["user_turn", "llm_request", "tts_node"].map(modelOf).filter((m): m is string => Boolean(m))
  const models: Hop = {
    icon: Brain,
    title: "Models",
    sub: model.pipeline !== "unknown" ? pipelineLabel[model.pipeline].split(" · ")[0] : "not reported",
    // One logo per model, the model name in its tooltip; a model we cannot place keeps its name.
    ...((stack.length ? stack : model.models).length
      ? {
          extra: (
            <div className="flex w-full flex-col gap-1.5">
              {(stack.length ? stack : model.models).map((m, k) => (
                <span key={`${m}${k}`} className="flex items-center gap-2 text-[12px]">
                  <ProviderLogo provider={providerOfModel(m)} size={16} />
                  <span className="font-mono">{m}</span>
                </span>
              ))}
            </div>
          ),
        }
      : { tags: ["not reported"] }),
    ...(onModels ? { onClick: onModels, hint: "Open the trace" } : {}),
  }
  const toModels: Link = { label: "provider APIs", secure: null, note: "The agent calls each model provider directly. That connection is not reported to LiveKit or to us." }

  let hops: Hop[]
  let links: Link[]
  if (isConsole) {
    hops = [
      { icon: Desktop, title: "Agent console", sub: "this machine", tags: ["no LiveKit room"] },
      { icon: hopIcons.agent, title: "Agent", sub: "local process" },
      models,
    ]
    links = [{ label: "local audio", secure: null, note: "Console mode plays and records on the developer's machine; no media leaves it for LiveKit." }, toModels]
  } else {
    // First hop: who called, and how their audio reached LiveKit.
    const trunk = sip?.sip?.trunkId ? data.sipTrunks[sip.sip.trunkId] : undefined
    const enc = trunk ? media[trunk.mediaEncryption] : undefined
    const answered = sip?.sip?.inviteMs ? sip.sip.statuses.find((s) => s.status === "active") : undefined
    const caller: Hop = sip
      ? {
          icon: hopIcons.phone,
          title: "Phone",
          sub: sip.sip?.trunkId ?? "trunk unknown",
          tags: [answered?.t != null ? `answered in ${secs(answered.t * 1000 - sip.sip!.inviteMs!)}` : "answer time unknown"],
          issues: enc === "RTP" ? [{ title: "Phone leg not encrypted (plain RTP)", severity: "medium" }] : [],
          ...(embedded ? {} : { onClick: () => navigate({ to: "/sip" }), hint: "Open the SIP page" }),
        }
      : { icon: Globe, title: room ? "Web or app" : "Caller", sub: room ? "WebRTC client" : "not watched" }
    const toRoom: Link = sip
      ? enc
        ? { label: enc, secure: enc !== "RTP", note: `Media encryption on trunk ${sip.sip?.trunkId} is ${enc === "RTP" ? "off, so phone audio reaches LiveKit as plain RTP" : enc}. The provider's transport is not in the API.` }
        : { label: "trunk not read", secure: null, note: "The trunk's media encryption setting was not read." }
      : room
        ? { label: "WebRTC", secure: true, note: "Inferred: WebRTC always encrypts media with DTLS-SRTP." }
        : unknown

    // The room and the agent's job in it.
    const agentP = room?.participants.filter((p) => p.kind === "agent") ?? []
    const dispatch = room?.dispatches?.find((d) => d.agentName && d.jobs.length)
    const failed = dispatch?.jobs.filter((j) => j.statuses.some((s) => s.status === "JS_FAILED")) ?? []
    const joinedAt = agentP[agentP.length - 1]?.joinedAtMs ?? null
    const join = dispatch?.createdAtMs != null && joinedAt !== null ? joinedAt - dispatch.createdAtMs : null
    const test = TEST_AGENTS.has(dispatch?.agentName ?? agentP[0]?.agentName ?? "")
    const lk: Hop = room
      ? {
          icon: hopIcons.room,
          title: "LiveKit room",
          sub: regionCity[agentP[0]?.region ?? room.participants[0]?.region ?? ""] ?? "region unknown",
          tags: [data.server ? `${data.server.edition} ${data.server.version}` : "version unknown", `${room.participants.length} people`],
          onClick: () => setRoomOpen(true),
          hint: "Open the room",
        }
      : { icon: hopIcons.room, title: "LiveKit room", sub: "not watched" }
    const agent: Hop = room
      ? {
          icon: hopIcons.agent,
          title: "Agent",
          sub: dispatch?.agentName ?? agentP[0]?.agentName ?? "name unknown",
          tags: [join !== null ? `joined ${secs(join)} after dispatch` : "join time unknown"],
          issues: [
            ...(failed.length ? [{ title: `${test ? "Test data: " : ""}${failed.length} job failed and LiveKit retried`, severity: "high" as const }] : []),
            ...(join !== null && join > JOIN_MS ? [{ title: `Joined ${secs(join)} after dispatch (over ${JOIN_MS / 1000} s)`, severity: "medium" as const }] : []),
          ],
          onClick: () => setRoomOpen(true),
          hint: "Open the room",
        }
      : { icon: hopIcons.agent, title: "Agent", sub: "not watched" }
    hops = [caller, lk, agent, models]
    links = [toRoom, room ? { label: "WebRTC", secure: true, note: "Inferred: the agent joins the room over WebRTC, which always encrypts media with DTLS-SRTP." } : unknown, toModels]
  }

  return (
    <div className="px-6 pt-4">
      <div className="flex justify-end pb-4">
        <PathLegend />
      </div>
      <PathDiagram path={{ hops, links }} vertical />
      <RoomDrawer room={room} ms={data} open={roomOpen} onOpenChange={setRoomOpen} />
    </div>
  )
}
