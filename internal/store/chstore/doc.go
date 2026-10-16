// Package chstore keeps what was measured in ClickHouse: today the raw events the worker stores,
// later calls, turns and spans. Migrations live in migrations/clickhouse.
//
// Rule: only the worker writes here (CLAUDE.md Boundaries); web reads.
package chstore
