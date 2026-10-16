// Package obs holds VoiceGateway's own observability: structured logs and the /healthz answer.
//
// Rule: logs are JSON through slog and never carry a request body, key or password
// (ARCHITECTURE.md Observability, CLAUDE.md Never do).
package obs
