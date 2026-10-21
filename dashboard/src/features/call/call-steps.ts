import { turnSpans, userTurnFor, numericAttr } from "./call-trace"
import { BUDGET_MS, TTS_START_MS } from "@/lib/thresholds"
import type { Call, Span } from "@/lib/api"
import { type CallModel, type TurnModel, fmtMs, fmtSec } from "./call-model"

// What happened inside one turn, step by step, from whatever the call carries: OTLP span attributes
// (prompt, reply, tokens, tools) and agent session events (speech, turn detection, overlap).
// Nothing is filled in: a field the source did not send is listed as missing, with the reason.

export type Status = "ok" | "warn" | "error" | "info"
export type Field = { label: string; value: string | null; why?: string; mono?: boolean }
export type Step = {
  id: string
  kind: "caller" | "eot" | "llm" | "tool" | "speech" | "overlap"
  title: string
  at: number | null
  status: Status
  summary: string
  input: Field[]
  output: Field[]
  details: Field[]
}

const spanSec = (s: Span) => (s.startMs !== null ? s.startMs / 1000 : null)
const attr = (s: Span | undefined, k: string) => s?.attrs?.[k] ?? null

// gen_ai messages are JSON arrays of {role, parts}; show them as "role: text" lines, or raw if they do not parse.
function messages(json: string | null): string | null {
  if (!json) return null
  try {
    const list = JSON.parse(json) as { role?: string; parts?: { type: string; content?: string; name?: string; arguments?: unknown; response?: unknown; result?: unknown }[] }[]
    return list
      .map((m) => {
        const body = (m.parts ?? [])
          .map((p) => (p.type === "text" ? p.content : p.type === "tool_call" ? `calls ${p.name}(${typeof p.arguments === "string" ? p.arguments : JSON.stringify(p.arguments)})` : p.type === "tool_call_response" ? `tool result ${JSON.stringify(p.response ?? p.result ?? "")}` : `[${p.type}]`))
          .join(" ")
        return `${m.role ?? "?"}: ${body}`
      })
      .join("\n")
  } catch {
    return json
  }
}
function instructions(json: string | null): string | null {
  if (!json) return null
  try {
    return (JSON.parse(json) as { content?: string }[]).map((p) => p.content ?? "").join("\n")
  } catch {
    return json
  }
}
const sec = (v: string | null) => (v === null || !Number.isFinite(Number(v)) || Number(v) < 0 ? null : fmtMs(Number(v) * 1000))

export function stepsFor(call: Call, model: CallModel, t: TurnModel): Step[] {
  const out: Step[] = []
  const noPrompt = model.source === "events" && !call.spans?.length ? "LiveKit's session events carry no prompt; it arrives with the OTLP spans of the observability upload (decision 0014)." : "Not in this call's data."
  const { root, children } = turnSpans(call, t.i)
  const ut = userTurnFor(call, root)
  const eouWait = ut.children.find((s) => s.name === "eou_wait")
  const eouDet = ut.children.find((s) => s.name === "eou_detection")

  if (t.userText !== null) {
    const conf = attr(ut.turn, "lk.transcript_confidence")
    const lowConf = conf !== null && Number(conf) < 0.8
    out.push({
      id: "caller", kind: "caller", title: "Caller turn", at: t.utterance?.start ?? (ut.turn ? spanSec(ut.turn) : t.window.start),
      status: lowConf ? "warn" : "ok",
      summary: `"${t.userText}"${conf !== null ? ` · confidence ${Number(conf).toFixed(2)}` : ""}`,
      input: [{ label: "Speech (VAD)", value: t.utterance ? fmtSec(t.utterance.end - t.utterance.start) : null, why: "No VAD events for this call" }],
      output: [
        { label: "Final transcript", value: attr(ut.turn, "lk.pii.user_transcript") ?? t.userText },
        { label: "Transcript confidence", value: conf !== null ? Number(conf).toFixed(3) : null, why: "Sent on the user_turn span (lk.transcript_confidence); not in this capture" },
      ],
      details: [
        { label: "Speech to text", value: attr(ut.turn, "gen_ai.request.model") ? `${attr(ut.turn, "gen_ai.provider.name")} · ${attr(ut.turn, "gen_ai.request.model")}` : null, why: "Not in this capture" },
        { label: "Transcript arrived after speech ended", value: call.turns?.[t.i]?.transcriptionDelayMs != null ? fmtMs(call.turns[t.i].transcriptionDelayMs!) : sec(attr(ut.turn, "lk.transcription_delay")), why: "Not reported" },
        { label: "Provider request id", value: attr(ut.turn, "lk.provider_request_ids"), mono: true, why: "Not in this capture" },
      ],
    })
  }

  if (t.eot || t.eotMs !== null || eouDet) {
    const p = numericAttr(eouDet, "lk.eou.probability") ?? t.eot?.probability ?? undefined
    const th = numericAttr(eouDet, "lk.eou.unlikely_threshold") ?? t.eot?.threshold ?? undefined
    const unsure = p !== undefined && th !== undefined && p < th
    const long = (t.eotMs ?? 0) >= 1500
    const outcome = attr(eouWait, "lk.eou.outcome")
    out.push({
      id: "eot", kind: "eot", title: "End of turn", at: eouWait ? spanSec(eouWait) : (t.eot?.t ?? t.utterance?.end ?? null),
      status: (unsure && long) || (outcome !== null && outcome !== "committed") ? "warn" : "ok",
      summary: `${p !== undefined && th !== undefined ? `Detector ${p.toFixed(2)} ${unsure ? "below" : "at or above"} its ${th.toFixed(3)} threshold` : "Turn ended"}${t.eotMs !== null ? `, agent waited ${fmtMs(t.eotMs)}` : ""}${outcome ? ` · ${outcome}` : ""}`,
      input: [{ label: "Caller stopped speaking", value: t.utterance ? `${t.utterance.end.toFixed(2)} s` : null, why: "No VAD events" }],
      output: [
        { label: "Detector probability", value: p !== undefined ? p.toFixed(3) : null, why: "No end-of-turn detection" },
        { label: "Threshold", value: th !== undefined ? th.toFixed(3) : null, why: "No end-of-turn detection" },
        { label: "Outcome", value: outcome, why: "On the eou_wait span (lk.eou.outcome); not in this capture" },
      ],
      details: [
        { label: "End-of-turn delay", value: t.eotMs !== null ? fmtMs(t.eotMs) : null, why: "Not reported" },
        { label: "Endpointing delay setting", value: sec(attr(eouWait, "lk.eou.endpointing_delay") ?? attr(eouDet, "lk.eou.endpointing_delay")), why: "Not in this capture" },
        { label: "Decided by", value: attr(eouWait, "lk.eou.source"), why: "Not in this capture" },
        { label: "Caller resumed after a false end", value: attr(eouWait, "lk.eou.rearm_count"), why: "Not in this capture" },
      ],
    })
  }

  for (const m of model.moments) {
    if ((m.kind === "backchannel" || m.kind === "interruption") && m.at.start >= t.window.start && m.at.start <= t.window.end && t.speaking && m.at.start >= t.speaking.start) {
      out.push({
        id: `ov${m.at.start}`, kind: "overlap", title: m.kind === "interruption" ? "Caller interrupted" : "Backchannel", at: m.at.start,
        status: m.kind === "interruption" ? "error" : "info",
        summary: m.kind === "interruption"
          ? `Agent stopped ${m.detectionMs !== null ? `${fmtMs(m.detectionMs)} ` : ""}after the caller started talking${m.inferred ? " (inferred from the reply's interrupted flag)" : ""}`
          : `Caller spoke over the agent; classified as a backchannel${m.detectionMs !== null ? ` in ${fmtMs(m.detectionMs)}` : ""}`,
        input: [{ label: "Overlap started", value: `${m.at.start.toFixed(2)} s` }],
        output: [{ label: "Classified as", value: m.kind }],
        details: [{ label: "Detection delay", value: m.detectionMs !== null ? fmtMs(m.detectionMs) : null, why: "Not reported" }],
      })
    }
  }

  const llmNodes = children.filter((s) => s.name === "llm_node")
  if (llmNodes.length) {
    llmNodes.forEach((n, k) => {
      const req = children.find((s) => s.parentId === n.spanId && s.name === "llm_request")
      const run = children.find((s) => req && s.parentId === req.spanId && s.name === "llm_request_run")
      const finish = attr(req, "gen_ai.response.finish_reasons")
      const failed = finish?.includes("error") ?? false
      const toolCall = finish?.includes("tool_call") ?? false
      const tokIn = attr(req, "gen_ai.usage.input_tokens")
      const tokOut = attr(req, "gen_ai.usage.output_tokens")
      const ttft = attr(req, "gen_ai.response.time_to_first_chunk") ?? attr(n, "lk.response.ttft")
      out.push({
        id: `llm${k}`, kind: "llm", title: `Model call${llmNodes.length > 1 ? ` ${k + 1}` : ""}${failed ? " · error" : toolCall ? " · tool call" : ""}`, at: spanSec(n),
        status: failed ? "warn" : "ok",
        summary: failed
          ? `Ended with finish reason "error"${n.durationMs !== null ? ` after ${fmtMs(n.durationMs)}` : ""}: the source does not identify why it ended, ${tokIn ?? "unknown"} tokens reported`
          : `${attr(n, "gen_ai.request.model") ?? "model"} ${toolCall ? "asked for a tool" : "answered"}${n.durationMs !== null ? ` in ${fmtMs(n.durationMs)}` : ""}, first token ${ttft !== null ? sec(ttft) : "unknown"}${tokIn !== null ? `, ${tokIn} in / ${tokOut} out tokens` : ""}`,
        input: [
          { label: "System instructions", value: instructions(attr(req, "gen_ai.system_instructions")), why: "Not sent" },
          { label: "Messages sent", value: messages(attr(req, "gen_ai.input.messages")) ?? attr(n, "lk.chat_ctx") ?? attr(n, "lk.pii.chat_ctx"), mono: true, why: "Not sent" },
          { label: "Tools offered", value: attr(n, "lk.function_tools"), mono: true, why: "Not sent" },
        ],
        output: [
          { label: "Reply", value: messages(attr(req, "gen_ai.output.messages")) ?? attr(n, "lk.response.text") ?? attr(n, "lk.pii.response.text"), why: "Not sent" },
          { label: "Finish reason", value: finish, why: "Not sent" },
        ],
        details: [
          { label: "Provider · model", value: `${attr(n, "gen_ai.provider.name") ?? "?"} · ${attr(req, "gen_ai.response.model") ?? attr(n, "gen_ai.request.model") ?? "?"}` },
          { label: "Tokens in / out", value: tokIn !== null ? `${tokIn} / ${tokOut}` : null, why: "Not sent" },
          { label: "Retries", value: attr(run, "lk.retry_count"), why: "Not sent" },
          { label: "Response id", value: attr(req, "gen_ai.response.id") ?? attr(run, "lk.provider_request_ids"), mono: true, why: "Not sent" },
        ],
      })
    })
  } else if (t.agentText !== null) {
    out.push({
      id: "llm", kind: "llm", title: "Model call", at: t.thinking?.start ?? (t.speaking ? t.speaking.start - 0.001 : null), status: "ok",
      summary: t.ttftMs !== null ? `First token in ${fmtMs(t.ttftMs)}` : "Reply produced; no timing for this model",
      input: [{ label: "Prompt and context", value: null, why: noPrompt }],
      output: [{ label: "Reply", value: t.agentText }],
      details: [
        { label: "First token", value: t.ttftMs !== null ? fmtMs(t.ttftMs) : null, why: model.pipeline === "realtime" ? "Realtime models report no per-message timings" : "Not reported" },
        { label: "Model", value: model.models[0] ?? null, why: "Not reported" },
      ],
    })
  }

  for (const s of children.filter((x) => x.name === "function_tool")) {
    const err = (attr(s, "lk.function_tool.is_error") ?? "").toLowerCase() === "true"
    out.push({
      id: s.spanId, kind: "tool", title: `Tool · ${attr(s, "lk.function_tool.name") ?? "unknown"}`, at: spanSec(s), status: err ? "error" : "ok",
      summary: `${err ? "Failed" : "Returned"}${s.durationMs !== null ? ` in ${fmtMs(s.durationMs)}` : ""}: ${attr(s, "lk.pii.function_tool.output") ?? attr(s, "lk.function_tool.output") ?? "no result sent"}`,
      input: [
        { label: "Arguments", value: attr(s, "lk.pii.function_tool.arguments") ?? attr(s, "lk.function_tool.arguments") ?? attr(s, "gen_ai.tool.call.arguments"), mono: true, why: "Not sent" },
        { label: "Tool description", value: attr(s, "gen_ai.tool.description"), why: "Not sent" },
      ],
      output: [{ label: "Result", value: attr(s, "lk.pii.function_tool.output") ?? attr(s, "lk.function_tool.output") ?? attr(s, "gen_ai.tool.call.result"), mono: true, why: "Not sent" }],
      details: [{ label: "Call id", value: attr(s, "lk.function_tool.id"), mono: true, why: "Not sent" }],
    })
  }

  const ttsNode = children.find((x) => x.name === "tts_node")
  const ttsReqs = children.filter((x) => x.name === "tts_request")
  if (t.speaking || t.agentText !== null) {
    const ttfb = attr(ttsNode, "lk.response.ttfb")
    const ttsMs = ttfb !== null ? Number(ttfb) * 1000 : t.ttsMs
    const playout = attr(root, "lk.playout.position")
    const interrupted = t.interrupted || attr(root, "lk.interrupted") === "true"
    out.push({
      id: "speech", kind: "speech", title: t.speaking ? "Agent spoke" : "Agent reply", at: t.speaking?.start ?? (ttsNode ? spanSec(ttsNode) : null),
      status: interrupted ? "error" : (ttsMs ?? 0) > TTS_START_MS ? "warn" : "ok",
      summary: interrupted
        ? `Cut off by the caller${playout ? ` after ${sec(playout)} of audio` : ""}`
        : `${t.speaking ? `Spoke for ${fmtSec(t.speaking.end - t.speaking.start)}` : "Reply generated; playback not observed"}${ttsMs !== null ? `, first audio after ${fmtMs(ttsMs)}` : ""}`,
      input: [{ label: "Text to speak", value: ttsReqs.length ? ttsReqs.map((r) => attr(r, "lk.pii.input_text")).filter(Boolean).join("\n") : t.agentText, why: "Not sent" }],
      output: [
        { label: "Heard by the caller", value: interrupted ? (playout ? `${sec(playout)} of audio played, then cut off` : null) : t.agentText, why: interrupted ? "Cut off: the played position (lk.playout.position) is on the agent_turn span, not in this capture" : "Not sent" },
      ],
      details: [
        { label: "Text to speech", value: attr(ttsNode, "gen_ai.request.model") ? `${attr(ttsNode, "gen_ai.provider.name")} · ${attr(ttsNode, "gen_ai.request.model")}` : null, why: model.pipeline === "realtime" ? "A realtime model has no separate TTS" : "Not in this capture" },
        { label: "TTS first audio", value: ttsMs !== null ? fmtMs(ttsMs) : null, why: model.pipeline === "realtime" ? "A realtime model has no separate TTS" : "Not reported" },
        { label: "TTS requests", value: ttsReqs.length ? String(ttsReqs.length) : null, why: "Not in this capture" },
        { label: "Interrupted", value: interrupted ? "yes" : "no" },
      ],
    })
  }
  return out.sort((a, b) => (a.at ?? Infinity) - (b.at ?? Infinity))
}

// Ranked findings for the whole call, each computed from the call's own data with the turns as evidence.
export type Finding = { id: string; severity: "high" | "medium" | "info"; title: string; detail: string; turns: number[] }

// Thresholds are settings (Settings → Thresholds), shared by every page.
export { BUDGET_MS } from "@/lib/thresholds"

// Match the persisted rule: exact native finish reason, never a substring match.
function hasErrorReason(raw: string | undefined): boolean {
  if (raw === "error") return true
  if (!raw) return false
  try {
    const reasons: unknown = JSON.parse(raw)
    return Array.isArray(reasons) && reasons.every((reason) => typeof reason === "string") && reasons.includes("error")
  } catch {
    return false
  }
}

export function findingsFor(call: Call, model: CallModel): Finding[] {
  const f: Finding[] = []
  type Scored = TurnModel & { eot: { probability: number; threshold: number } }
  const scored = (t: TurnModel): t is Scored => t.eot !== null && t.eot.probability !== null && t.eot.threshold !== null
  const waits = model.turns.filter(scored).filter((t) => t.eot.probability < t.eot.threshold && (t.eotMs ?? 0) >= 1500)
  if (waits.length) {
    const avg = waits.reduce((a, t) => a + (t.eotMs ?? 0), 0) / waits.length
    f.push({
      id: "eot", severity: "high",
      title: `The agent waited ${fmtMs(avg)} for the end of the turn on ${waits.length} of ${model.turns.filter((t) => t.userText !== null).length} turns`,
      detail: `Reported end-of-turn delay averaged ${fmtMs(avg)}. Detector scores were below their reported thresholds (${waits.map((t) => `${t.eot.probability.toFixed(2)} < ${t.eot.threshold.toFixed(2)}`).join(", ")}). These measurements do not establish which endpointing setting caused the delay.`,
      turns: waits.map((t) => t.i),
    })
  }
  const cut = model.turns.filter((t) => t.interrupted)
  if (cut.length) {
    f.push({
      id: "cut", severity: "high",
      title: `${cut.length} of ${model.turns.length} replies were cut off by the caller`,
      detail: `The caller talked over ${cut.length === 1 ? "turn" : "turns"} ${cut.map((t) => t.i + 1).join(", ")}. Short, front-loaded replies or a lower speaking rate usually help; check whether the replies were too long for the question.`,
      turns: cut.map((t) => t.i),
    })
  }
  for (const m of model.moments) {
    if (m.kind !== "backchannel") continue
    const answered = model.turns.find((t) => t.utterance && Math.abs(t.utterance.start - m.at.start) < 1 && t.userText && t.userText.split(/\s+/).length <= 2)
    if (answered) {
      f.push({
        id: `bc${answered.i}`, severity: "medium",
        title: `Possible backchannel response (turn ${answered.i + 1})`,
        detail: `A reported backchannel overlaps the estimated start of the short utterance "${answered.userText}". This timing-based match suggests a response to a backchannel; it does not establish the cause or the effective backchannel_boundary setting.`,
        turns: [answered.i],
      })
    }
  }
  const slow = model.turns.filter((t) => t.responseMs !== null && t.responseMs > BUDGET_MS)
  if (slow.length) {
    f.push({ id: "slow", severity: "medium", title: `${slow.length} turns took longer than ${fmtMs(BUDGET_MS)} to answer`, detail: `Turns ${slow.map((t) => t.i + 1).join(", ")}. The budget is a setting, not a measurement.`, turns: slow.map((t) => t.i) })
  }
  const late = (call.turns ?? []).map((t, i) => ({ t, i })).filter(({ t }) => (t.transcriptionDelayMs ?? 0) > 1000)
  if (late.length) {
    f.push({ id: "late", severity: "medium", title: `The transcript arrived late on ${late.length === 1 ? `turn ${late[0].i + 1}` : `${late.length} turns`}`, detail: `Speech to text finished ${late.map(({ t }) => fmtMs(t.transcriptionDelayMs!)).join(", ")} after the caller stopped. Everything after it waits.`, turns: late.map(({ i }) => i) })
  }
  const reqs = (call.spans ?? []).filter((s) => s.name === "llm_request")
  const discarded = reqs.filter((s) => hasErrorReason(s.attrs?.["gen_ai.response.finish_reasons"]))
  if (discarded.length) {
    f.push({ id: "discard", severity: "medium", title: `${discarded.length} of ${reqs.length} model calls reported an error`, detail: `They reported finish reason "error". Inspect the request and its parent turn to distinguish provider failure from canceled preemptive generation. This flag alone does not establish the cause.`, turns: model.turns.filter((t) => turnSpans(call, t.i).children.some((s) => discarded.some((r) => r.spanId === s.spanId))).map((t) => t.i) })
  }
  if (reqs.length && reqs.every((s) => s.attrs?.["gen_ai.usage.input_tokens"] === "0")) {
    f.push({ id: "zero", severity: "info", title: "The model reported 0 tokens", detail: `All ${reqs.length} model calls reported zero input tokens. Output usage is independent; zero alone does not establish a provider failure.`, turns: [] })
  }
  const slowTts = model.turns.filter((t) => (t.ttsMs ?? 0) > TTS_START_MS)
  if (slowTts.length) {
    f.push({ id: "tts", severity: "medium", title: `Text to speech took over ${fmtMs(TTS_START_MS)} to start on ${slowTts.length} of ${model.turns.length} turns`, detail: `First audio came after ${slowTts.map((t) => fmtMs(t.ttsMs!)).join(", ")}. That is time the caller hears silence; a streaming TTS or a faster voice model cuts it.`, turns: slowTts.map((t) => t.i) })
  }
  const stalls = (call.spans ?? []).filter((s) => s.name === "event_loop_blocked")
  if (stalls.length) {
    const max = Math.max(...stalls.map((s) => Number(s.attrs?.["lk.blocking.duration"] ?? 0)))
    f.push({ id: "stall", severity: "medium", title: `The agent's process stalled ${stalls.length} times, up to ${fmtMs(max * 1000)}`, detail: "LiveKit flagged its event loop as blocked. While it is blocked the agent cannot hear, think or speak; the stack is on each event_loop_blocked span.", turns: [] })
  }
  if (model.pipeline === "realtime") {
    f.push({ id: "rt", severity: "info", title: "This call uses a realtime model", detail: "Realtime inference combines speech and language processing. Inspect its reported metrics; separate STT, LLM and TTS stages may not exist.", turns: [] })
  }
  return f
}

// A successful check requires complete evidence. Missing flags never imply success.
export function evidenceCheck(spans: Span[], key: string, passes: (value: string) => boolean): boolean | null {
  const values = spans.map((s) => s.attrs?.[key])
  if (values.some((v) => v != null && v !== "" && !passes(v))) return false
  return values.length && values.every((v) => v != null && v !== "") ? true : null
}
