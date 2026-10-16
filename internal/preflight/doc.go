// Package preflight checks that every dependency answers before the roles start.
//
// Rule: every check runs, each with its own timeout, and every failure is reported, so one
// unreachable service never hides another (ARCHITECTURE.md Observability).
package preflight
