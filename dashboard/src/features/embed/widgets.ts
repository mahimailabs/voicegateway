// The widgets a platform can put in its own product (decision 0023): compact tiles in iPhone widget
// sizes, each answering one question from one read. `sizes` are the frame sizes it comes in; the
// one-call view is the page a recent call opens, full size. `with` names the widgets its token
// also needs, so links inside the frame work.
export const sizes = {
  small: { width: 170, height: 170 },
  medium: { width: 364, height: 170 },
  large: { width: 364, height: 382 },
  full: { width: 760, height: 760 },
} as const
export type Size = keyof typeof sizes

export const widgets = [
  { id: "answer-time", title: "Answer time", question: "How fast do the agents answer once the caller stops talking?", sizes: ["small", "medium"], with: [] },
  { id: "calls", title: "Calls", question: "How many calls did the agents take, by day, over phone or web?", sizes: ["small", "medium"], with: [] },
  { id: "issues", title: "Issues", question: "Is anything wrong right now, and what?", sizes: ["small", "medium", "large"], with: [] },
  { id: "recent-calls", title: "Recent calls", question: "What just happened on the line? A row opens that call.", sizes: ["medium", "large"], with: ["call"] },
  { id: "time-per-turn", title: "Time per turn", question: "Where does a turn's time go between the caller and the agent's voice?", sizes: ["medium", "large"], with: [] },
  { id: "call", title: "One call", question: "One call's transcript, timeline and recording. Pass &call=<call id>.", sizes: ["full"], with: [] },
] as const satisfies readonly { id: string; title: string; question: string; sizes: readonly Size[]; with: readonly string[] }[]

export type WidgetId = (typeof widgets)[number]["id"]
