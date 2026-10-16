// Package app wires the roles together: it builds the real clients from the configuration and
// hands them to the packages that use them.
//
// Rule: wiring only, by constructor injection; no other package constructs a dependency it does not
// own (CLAUDE.md Boundaries, decision 0009).
package app
