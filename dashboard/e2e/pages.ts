import capture from "../src/mocks/capture-session.json" with { type: "json" }

/**
 * Every console page, in sidebar order: the one list the shell checks (shell.spec.ts) and the screen
 * catalog (screens.spec.ts) both walk. The shell check fails when a sidebar link is missing here, so
 * the catalog cannot fall behind the sidebar.
 *
 * `heading` is what the page shows once loaded (null: Live has no heading, its room filter is waited
 * for instead). `group` is the sidebar section, `source` the file that draws the page.
 */
export type ConsolePage = {
  path: string
  title: string
  heading: string | RegExp | null
  group: "Overview" | "Observe" | "Agents" | "LiveKit" | "Project" | "Settings" | "Docs"
  source: string
}

const call = "calls/" + encodeURIComponent(capture.call.room)

export const scope = "/org/default/project/default-project/"

export const pages: ConsolePage[] = [
  { path: "overview", title: "Overview", heading: "Overview", group: "Overview", source: "src/features/overview/overview.tsx" },
  { path: "live", title: "Live", heading: null, group: "Observe", source: "src/features/live/live-page.tsx" },
  { path: "calls", title: "Calls", heading: "Calls", group: "Observe", source: "src/features/calls/calls-page.tsx" },
  { path: call, title: "Call detail", heading: capture.call.room, group: "Observe", source: "src/features/calls/calls-page.tsx" },
  { path: call + "?callView=diagnosis", title: "Call diagnosis", heading: capture.call.room, group: "Observe", source: "src/features/calls/calls-page.tsx" },
  { path: "issues", title: "Issues", heading: "Issues", group: "Observe", source: "src/features/issues/issues-page.tsx" },
  { path: "alerts", title: "Alerts", heading: "Alerts", group: "Observe", source: "src/features/alerts/alerts-page.tsx" },
  { path: "agents", title: "Agent workers", heading: "Agent workers", group: "Agents", source: "src/features/agents/agents-page.tsx" },
  { path: "sfu", title: "Media server", heading: "Media server", group: "LiveKit", source: "src/features/media-server/media-server-page.tsx" },
  { path: "sip", title: "Phone bridge", heading: "Phone bridge", group: "LiveKit", source: "src/features/sip/sip-page.tsx" },
  { path: "egress", title: "Egress", heading: "Egress", group: "LiveKit", source: "src/features/egress/egress-page.tsx" },
  { path: "ingress", title: "Ingress", heading: "Ingress", group: "LiveKit", source: "src/features/ingress/ingress-page.tsx" },
  { path: "project", title: "Project", heading: /Everything this project/, group: "Project", source: "src/features/project/project-page.tsx" },
  { path: "settings", title: "Settings", heading: "Settings", group: "Settings", source: "src/features/settings/settings-dialog.tsx" },
  { path: "docs", title: "API reference", heading: "VoiceGateway API", group: "Docs", source: "src/features/api-docs/api-docs-page.tsx" },
]
