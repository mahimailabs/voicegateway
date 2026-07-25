// Agents that have already had an auto-probe dispatched during this page load.
//
// Module scope on purpose: a probe places one real, billed call, so the
// "run once, then cache" default must not re-bill. This set survives component
// remounts (navigating Overview <-> Agents and back) and is SHARED across both
// surfaces, so whichever renders an eligible, uncached agent first places the
// single call; the other sees it here and skips.
//
// A hard browser refresh resets this set, but by then the result is cached
// server-side, so the first /api/agents response carries `latency_probe` and the
// `!latency_probe` guard at the call sites skips the agent anyway. The server's
// per-agent cooldown + in-flight lock are the final backstop against any race.
export const attemptedProbes = new Set<string>();
