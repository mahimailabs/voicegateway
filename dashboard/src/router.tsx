import { IssuesPage } from "@/features/issues/issues-page"
import { Outlet, createRootRoute, createRoute, createRouter, lazyRouteComponent, redirect } from "@tanstack/react-router"
import { AppShell } from "@/components/app-shell"
import { CallsPage } from "@/features/calls/calls-page"
import { OverviewPage } from "@/features/overview/overview"
import { ProjectPage } from "@/features/project/project-page"
import { AgentsPage } from "@/features/agents/agents-page"
import { LivePage } from "@/features/live/live-page"
import { AlertsPage } from "@/features/alerts/alerts-page"
import { IngressPage } from "@/features/ingress/ingress-page"
import { EgressPage } from "@/features/egress/egress-page"
import { SipPage } from "@/features/sip/sip-page"
import { MediaServerPage } from "@/features/media-server/media-server-page"
import { EmbedPage } from "@/features/embed/embed-page"
import { entryPath, scopeRewrite } from "@/lib/url-state"

// The console pages sit inside the app shell; embeddable widgets (decision 0023) render on their own.
const root = createRootRoute({ component: Outlet })
const app = createRoute({ getParentRoute: () => root, id: "app", component: AppShell })
const embed = createRoute({ getParentRoute: () => root, path: "/embed/$widget", component: EmbedPage })
// The API reference stands alone like a widget, loaded only when opened: Scalar is large.
const apiDocs = createRoute({
  getParentRoute: () => root,
  path: "/docs",
  component: lazyRouteComponent(() => import("@/features/api-docs/api-docs-page"), "ApiDocsPage"),
})
const index = createRoute({
  getParentRoute: () => app,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/overview" })
  },
})
const project = createRoute({ getParentRoute: () => app, path: "/project", component: ProjectPage })
const overview = createRoute({ getParentRoute: () => app, path: "/overview", component: OverviewPage })
const agents = createRoute({ getParentRoute: () => app, path: "/agents", component: AgentsPage })
const egress = createRoute({ getParentRoute: () => app, path: "/egress", component: EgressPage })
const ingress = createRoute({ getParentRoute: () => app, path: "/ingress", component: IngressPage })
const calls = createRoute({ getParentRoute: () => app, path: "/calls", component: CallsPage })
const callRoute = createRoute({ getParentRoute: () => app, path: "/calls/$callId", component: CallsPage })
const issues = createRoute({ getParentRoute: () => app, path: "/issues", component: IssuesPage })
const alerts = createRoute({ getParentRoute: () => app, path: "/alerts", component: AlertsPage })
const live = createRoute({ getParentRoute: () => app, path: "/live", component: LivePage })
const sfu = createRoute({ getParentRoute: () => app, path: "/sfu", component: MediaServerPage })
const sip = createRoute({ getParentRoute: () => app, path: "/sip", component: SipPage })
const settings = createRoute({ getParentRoute: () => app, path: "/settings", component: OverviewPage })

// A bare link is moved under the current project before the router reads it. The workspaces are read
// first (main.tsx), so the shell can tell a project that does not exist from one it may read.
{
  const next = entryPath(location)
  if (next) history.replaceState(history.state, "", next)
}

export const router = createRouter({
  rewrite: scopeRewrite,
  routeTree: root.addChildren([app.addChildren([index, project, overview, calls, callRoute, live, issues, alerts, agents, sfu, sip, egress, ingress, settings]), embed, apiDocs]),
})

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}
