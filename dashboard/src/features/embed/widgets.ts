// The widgets a platform can put in its own product (decision 0023). Each is an existing console
// view without the shell; `height` is what the snippet suggests for the frame.
export const widgets = [
  { id: "answer-time", title: "Answer time", description: "Median time from the caller stopping to the agent's first audio, against the budget.", height: 340 },
  { id: "turn-anatomy", title: "Turn anatomy", description: "Where a turn's time goes, stage by stage, median to p90.", height: 600 },
  { id: "calls", title: "Calls", description: "Every call with its length, turns, answer time and issues. A row opens the call.", height: 560 },
  { id: "call", title: "One call", description: "One call's transcript, insights and recording timeline. Pass ?call=<call id>.", height: 760 },
  { id: "issues", title: "Issues", description: "Issues grouped across calls and components, by severity.", height: 440 },
] as const

export type WidgetId = (typeof widgets)[number]["id"]
