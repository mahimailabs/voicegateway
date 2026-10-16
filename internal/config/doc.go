// Package config reads VoiceGateway's settings from VOICEGATEWAY_* environment variables.
//
// Rule: a missing required variable stops the binary at start and is named; secrets never appear in
// errors or logs, so connection URLs are only ever shown redacted (decision 0009, ARCHITECTURE.md
// Security and Privacy).
package config
