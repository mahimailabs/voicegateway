// Package turn builds a call's turns, their per-stage latencies and their usage from the spans and
// session report the worker stored (phase 04b, decisions 0010 and 0014). It does no I/O.
//
// Rules: one turn per agent_turn span, never one invented from text alone; a value no span
// reported, or reported as LiveKit's "unavailable" sentinel, stays unknown (nil), while any other
// zero is a measured zero; the LLM time of a turn is the generation that was spoken, not an
// earlier one discarded by preemptive generation; and a usage total says how many requests did
// not report it, so a partial sum is never read as a complete one.
//
// Attribute keys mirror livekit/agents/telemetry/trace_types.py in LiveKit Agents 1.8.7.
package turn
