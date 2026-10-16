// Package migrations embeds the database migrations in the binary, so `voicegateway migrate` needs
// no files on disk. Migrations only add (ARCHITECTURE.md Deployment).
package migrations

import "embed"

// Postgres holds the goose migrations for Postgres, under postgres/.
//
//go:embed postgres/*.sql
var Postgres embed.FS

// ClickHouse holds the goose migrations for ClickHouse, under clickhouse/.
//
//go:embed clickhouse/*.sql
var ClickHouse embed.FS
