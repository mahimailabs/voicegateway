// Package alert is the worker's alert delivery (decision 0020, docs/specs/alerts-behaviour.md and
// alerts-delivery.md). Evaluator turns the occurrences the Issues page stores into incidents, one per
// rule and subject, and decides when each fires, reminds and resolves. Deliverer sends each incident
// change to the project's email and webhook channels through durable deliveries in Postgres.
//
// Rules: notifications go per incident, never per occurrence; an occurrence counts once per rule; a
// source that stops reporting never resolves an incident (unknown is not healthy); delivery is at
// least once, keyed by incident, change and channel; a channel's URL, secret or recipients never
// appear in a log, a stored error or an API response.
package alert
