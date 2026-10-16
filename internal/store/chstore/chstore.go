package chstore

import (
	"context"
	"errors"
	"fmt"
	"io/fs"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/ClickHouse/clickhouse-go/v2/lib/proto"
	"github.com/pressly/goose/v3"

	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/migrations"
)

// Store reads and writes ClickHouse.
type Store struct {
	conn driver.Conn
}

// Open connects to ClickHouse at a clickhouse:// URL. The connection is lazy: Open succeeds while
// ClickHouse is still starting.
func Open(url string) (*Store, error) {
	opts, err := clickhouse.ParseDSN(url)
	if err != nil {
		return nil, fmt.Errorf("parse ClickHouse URL: %w", err)
	}
	conn, err := clickhouse.Open(opts)
	if err != nil {
		return nil, fmt.Errorf("open ClickHouse: %w", err)
	}
	return &Store{conn: conn}, nil
}

// Close releases the connections.
func (s *Store) Close() error { return s.conn.Close() }

// Ping reports whether ClickHouse answers.
func (s *Store) Ping(ctx context.Context) error { return s.conn.Ping(ctx) }

// Migrate applies every pending migration in migrations/clickhouse and returns how many ran.
func Migrate(ctx context.Context, url string) (int, error) {
	opts, err := clickhouse.ParseDSN(url)
	if err != nil {
		return 0, fmt.Errorf("parse ClickHouse URL: %w", err)
	}
	db := clickhouse.OpenDB(opts)
	defer func() { _ = db.Close() }()
	fsys, err := fs.Sub(migrations.ClickHouse, "clickhouse")
	if err != nil {
		return 0, fmt.Errorf("migrations: %w", err)
	}
	provider, err := goose.NewProvider(goose.DialectClickHouse, db, fsys)
	if err != nil {
		return 0, fmt.Errorf("migrations: %w", err)
	}
	results, err := provider.Up(ctx)
	if err != nil {
		return len(results), fmt.Errorf("migrate clickhouse: %w", err)
	}
	return len(results), nil
}

// Existing returns which of keys are already stored.
func (s *Store) Existing(ctx context.Context, keys []store.EventKey) (map[store.EventKey]bool, error) {
	type scope struct{ org, project, kind string }
	byScope := map[scope][]string{}
	for _, k := range keys {
		sc := scope{k.OrganisationID, k.ProjectID, k.Kind}
		byScope[sc] = append(byScope[sc], k.DedupeKey)
	}
	found := map[store.EventKey]bool{}
	for sc, ids := range byScope {
		rows, err := s.conn.Query(ctx,
			`SELECT dedupe_key FROM events WHERE organisation_id = ? AND project_id = ? AND kind = ? AND dedupe_key IN ?`,
			sc.org, sc.project, sc.kind, ids)
		if err != nil {
			return nil, fmt.Errorf("find stored events: %w", err)
		}
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				_ = rows.Close()
				return nil, fmt.Errorf("find stored events: %w", err)
			}
			found[store.EventKey{OrganisationID: sc.org, ProjectID: sc.project, Kind: sc.kind, DedupeKey: id}] = true
		}
		err = rows.Err()
		_ = rows.Close()
		if err != nil {
			return nil, fmt.Errorf("find stored events: %w", err)
		}
	}
	return found, nil
}

// Insert writes events in one batch. Either every row lands or the call fails.
func (s *Store) Insert(ctx context.Context, events []store.Event) error {
	if len(events) == 0 {
		return nil
	}
	batch, err := s.conn.PrepareBatch(ctx,
		`INSERT INTO events (organisation_id, project_id, kind, dedupe_key, event_id, stream_id, received_at, payload)`)
	if err != nil {
		return fmt.Errorf("prepare insert: %w", err)
	}
	for _, e := range events {
		if err := batch.Append(e.OrganisationID, e.ProjectID, e.Kind, e.DedupeKey, e.EventID, e.StreamID, e.ReceivedAt, string(e.Payload)); err != nil {
			_ = batch.Abort()
			// Append fails only when a value does not convert to its column.
			return fmt.Errorf("insert event %s: %w: %w", e.StreamID, store.ErrBadRow, err)
		}
	}
	if err := batch.Send(); err != nil {
		var ex *proto.Exception
		if errors.As(err, &ex) && dataErrors[ex.Code] {
			return fmt.Errorf("insert %d events: %w: %w", len(events), store.ErrBadRow, err)
		}
		return fmt.Errorf("insert %d events: %w", len(events), err)
	}
	return nil
}

// dataErrors are the ClickHouse error codes that blame the data rather than the server
// (src/Common/ErrorCodes.cpp). Every other failure, such as TOO_MANY_PARTS, a memory limit or a
// timeout, is treated as transient and retried.
var dataErrors = map[int32]bool{
	6:   true, // CANNOT_PARSE_TEXT
	27:  true, // CANNOT_PARSE_INPUT_ASSERTION_FAILED
	38:  true, // CANNOT_PARSE_DATE
	41:  true, // CANNOT_PARSE_DATETIME
	53:  true, // TYPE_MISMATCH
	69:  true, // ARGUMENT_OUT_OF_BOUND
	70:  true, // CANNOT_CONVERT_TYPE
	72:  true, // CANNOT_PARSE_NUMBER
	117: true, // INCORRECT_DATA
	321: true, // VALUE_IS_OUT_OF_RANGE_OF_DATA_TYPE
}
