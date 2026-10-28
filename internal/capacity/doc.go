// Package capacity runs the capacity test (decision 0022, docs/specs/capacity-runner.md): from a
// person's click, a worker ramps synthetic calls at one agent, 1, 2, 4 and so on, until a stop rule
// trips, and stores each step as it lands.
//
// Rules: a run starts only from a request, never on a schedule; it refuses a busy agent and stops if
// a room it did not create gets the agent; the tester's own limit is reported as the tester's, never
// as the agent's; every room it creates is deleted when it ends, whatever ended it; and a value it
// did not measure stays unknown.
package capacity
