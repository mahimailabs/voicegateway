package chstore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"slices"
	"strings"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/ClickHouse/clickhouse-go/v2/lib/proto"
	"github.com/pressly/goose/v3"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/turn"
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

// Insert writes events with their spans, trace links and unmatched records. Those go first and
// the events last, so a stored event means everything joined to it is stored; every table folds a
// repeated row, so a retry after a partial write leaves one of each.
func (s *Store) Insert(ctx context.Context, events []store.Event) error {
	if len(events) == 0 {
		return nil
	}
	if err := s.insertSpans(ctx, events); err != nil {
		return err
	}
	if err := s.insertTraceCalls(ctx, events); err != nil {
		return err
	}
	if err := s.insertUnmatched(ctx, events); err != nil {
		return err
	}
	if err := s.insertMetricPoints(ctx, events); err != nil {
		return err
	}
	return s.send(ctx, "events", len(events),
		`INSERT INTO events (organisation_id, project_id, kind, call_id, dedupe_key, event_id, stream_id, received_at, payload)`,
		func(b driver.Batch) error {
			for _, e := range events {
				if err := b.Append(e.OrganisationID, e.ProjectID, e.Kind, e.CallID, e.DedupeKey, e.EventID, e.StreamID, e.ReceivedAt, string(e.Payload)); err != nil {
					return fmt.Errorf("event %s: %w", e.StreamID, err)
				}
			}
			return nil
		})
}

func (s *Store) insertSpans(ctx context.Context, events []store.Event) error {
	n := 0
	for _, e := range events {
		n += len(e.Spans)
	}
	if n == 0 {
		return nil
	}
	return s.send(ctx, "spans", n,
		`INSERT INTO spans (organisation_id, project_id, trace_id, span_id, parent_span_id, call_id, name, kind, start_time, end_time, status_code, room_id, job_id, scope_name, attributes, resource, stream_id)`,
		func(b driver.Batch) error {
			for _, e := range events {
				for _, sp := range e.Spans {
					if err := b.Append(e.OrganisationID, e.ProjectID, sp.TraceID, sp.SpanID, sp.ParentSpanID, sp.CallID, sp.Name, sp.Kind,
						orNil(sp.Start), orNil(sp.End), sp.StatusCode, sp.RoomID, sp.JobID, sp.ScopeName, nonNil(sp.Attributes), nonNil(sp.Resource), e.StreamID); err != nil {
						return fmt.Errorf("span of %s: %w", e.StreamID, err)
					}
				}
			}
			return nil
		})
}

func (s *Store) insertTraceCalls(ctx context.Context, events []store.Event) error {
	n := 0
	for _, e := range events {
		n += len(e.TraceCalls)
	}
	if n == 0 {
		return nil
	}
	return s.send(ctx, "trace links", n, `INSERT INTO trace_calls (organisation_id, project_id, trace_id, call_id)`,
		func(b driver.Batch) error {
			for _, e := range events {
				for _, tc := range e.TraceCalls {
					if err := b.Append(e.OrganisationID, e.ProjectID, tc.TraceID, tc.CallID); err != nil {
						return fmt.Errorf("trace link of %s: %w", e.StreamID, err)
					}
				}
			}
			return nil
		})
}

func (s *Store) insertUnmatched(ctx context.Context, events []store.Event) error {
	n := 0
	for _, e := range events {
		n += len(e.Unmatched)
	}
	if n == 0 {
		return nil
	}
	return s.send(ctx, "unmatched", n, `INSERT INTO unmatched (organisation_id, project_id, kind, key, reason)`,
		func(b driver.Batch) error {
			for _, e := range events {
				for _, u := range e.Unmatched {
					if err := b.Append(e.OrganisationID, e.ProjectID, u.Kind, u.Key, u.Reason); err != nil {
						return fmt.Errorf("unmatched of %s: %w", e.StreamID, err)
					}
				}
			}
			return nil
		})
}

func (s *Store) insertMetricPoints(ctx context.Context, events []store.Event) error {
	n := 0
	for _, e := range events {
		n += len(e.MetricPoints)
	}
	if n == 0 {
		return nil
	}
	return s.send(ctx, "metric points", n,
		`INSERT INTO metric_points (organisation_id, project_id, name, call_id, value, start_time, end_time, room_id, job_id, attributes, attributes_key, stream_id)`,
		func(b driver.Batch) error {
			for _, e := range events {
				for _, p := range e.MetricPoints {
					if err := b.Append(e.OrganisationID, e.ProjectID, p.Name, p.CallID, p.Value, p.Start, p.End, p.RoomID, p.JobID, nonNil(p.Attributes), attributesKey(p.Attributes), e.StreamID); err != nil {
						return fmt.Errorf("metric point of %s: %w", e.StreamID, err)
					}
				}
			}
			return nil
		})
}

// send prepares one insert, lets fill append its rows and sends it. An append that fails, or a
// data error from ClickHouse, wraps store.ErrBadRow; any other failure is transient.
func (s *Store) send(ctx context.Context, what string, n int, query string, fill func(driver.Batch) error) error {
	batch, err := s.conn.PrepareBatch(ctx, query)
	if err != nil {
		return fmt.Errorf("prepare insert of %s: %w", what, err)
	}
	if err := fill(batch); err != nil {
		_ = batch.Abort()
		// Append fails only when a value does not convert to its column.
		return fmt.Errorf("insert %s: %w: %w", what, store.ErrBadRow, err)
	}
	if err := batch.Send(); err != nil {
		var ex *proto.Exception
		if errors.As(err, &ex) && dataErrors[ex.Code] {
			return fmt.Errorf("insert %d %s: %w: %w", n, what, store.ErrBadRow, err)
		}
		return fmt.Errorf("insert %d %s: %w", n, what, err)
	}
	return nil
}

// orNil is nil for a time the exporter did not send, stored as null.
func orNil(t time.Time) *time.Time {
	if t.IsZero() {
		return nil
	}
	return &t
}

// attributesKey is a map as sorted key=value lines: equal maps give equal keys.
func attributesKey(m map[string]string) string {
	keys := slices.Sorted(maps.Keys(m))
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k)
		b.WriteByte('=')
		b.WriteString(m[k])
		b.WriteByte('\n')
	}
	return b.String()
}

func nonNil(m map[string]string) map[string]string {
	if m == nil {
		return map[string]string{}
	}
	return m
}

// UnlinkedTraceKind is the unmatched kind of a trace none of whose spans joined a call.
const UnlinkedTraceKind = "trace"

// unlinkedLookback bounds how far back the unlinked-trace sweep reads spans, so each sweep scans a
// day of partitions rather than every span ever stored. A trace whose last span is older was
// counted by an earlier sweep, unless the worker was down for the whole day.
const unlinkedLookback = 24 * time.Hour

// CountUnlinkedTraces records in unmatched, once each, every trace whose last span ended before
// lastSpanBefore (when it was stored, if no span reported an end), none of whose spans joined a
// call and that no trace link names. It returns how many it recorded. Readers count unmatched
// with FINAL: two workers sweeping at once can write the same trace before a merge folds it.
func (s *Store) CountUnlinkedTraces(ctx context.Context, lastSpanBefore time.Time) (uint64, error) {
	const unlinked = `
		SELECT organisation_id, project_id, toString(trace_id) AS key
		FROM spans FINAL
		WHERE ifNull(start_time, inserted_at) >= @since
		  AND (organisation_id, project_id, trace_id) NOT IN (SELECT organisation_id, project_id, trace_id FROM trace_calls)
		GROUP BY organisation_id, project_id, trace_id
		HAVING ifNull(max(end_time), max(inserted_at)) < @before AND countIf(call_id != '') = 0
		   AND (organisation_id, project_id, key) NOT IN (SELECT organisation_id, project_id, key FROM unmatched WHERE kind = @kind)`
	args := []any{
		clickhouse.Named("since", lastSpanBefore.Add(-unlinkedLookback)),
		clickhouse.Named("before", lastSpanBefore),
		clickhouse.Named("kind", UnlinkedTraceKind),
	}
	var n uint64
	if err := s.conn.QueryRow(ctx, `SELECT count() FROM (`+unlinked+`)`, args...).Scan(&n); err != nil {
		return 0, fmt.Errorf("count unlinked traces: %w", err)
	}
	if n == 0 {
		return 0, nil
	}
	err := s.conn.Exec(ctx, `INSERT INTO unmatched (organisation_id, project_id, kind, key, reason)
		SELECT organisation_id, project_id, @kind, key, 'unlinked 15 minutes after its last span' FROM (`+unlinked+`)`, args...)
	if err != nil {
		return 0, fmt.Errorf("record unlinked traces: %w", err)
	}
	return n, nil
}

// SpanCalls returns the call each span of a trace resolves to: its own call id, else the call its
// trace is linked to, else empty.
func (s *Store) SpanCalls(ctx context.Context, organisationID, projectID, traceID string) (map[string]string, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT span_id, if(call_id != '', call_id,
			(SELECT any(call_id) FROM trace_calls FINAL WHERE organisation_id = @org AND project_id = @project AND trace_id = @trace))
		FROM spans FINAL WHERE organisation_id = @org AND project_id = @project AND trace_id = @trace`,
		clickhouse.Named("org", organisationID), clickhouse.Named("project", projectID), clickhouse.Named("trace", traceID))
	if err != nil {
		return nil, fmt.Errorf("span calls: %w", err)
	}
	defer func() { _ = rows.Close() }()
	out := map[string]string{}
	for rows.Next() {
		var span, call string
		if err := rows.Scan(&span, &call); err != nil {
			return nil, fmt.Errorf("span calls: %w", err)
		}
		out[span] = call
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("span calls: %w", err)
	}
	return out, nil
}

// CallTurns builds a call's turns and usage from every span that resolves to it, by its own call
// id or through its trace's link, and from the chat history of its latest session report. Turns are
// built at read time, so spans stored late or out of order change the answer without a rewrite.
func (s *Store) CallTurns(ctx context.Context, organisationID, projectID, callID string) (turn.Result, error) {
	if callID == "" {
		// An empty id is what unjoined rows carry; it names no call.
		return turn.Result{}, nil
	}
	spans, err := s.CallSpans(ctx, organisationID, projectID, callID)
	if err != nil {
		return turn.Result{}, err
	}
	history, err := s.chatHistory(ctx, organisationID, projectID, callID)
	if err != nil {
		return turn.Result{}, err
	}
	res := turn.Build(spans, history)
	// STT audio has no per-turn source in LiveKit Agents 1.8.7; the call's comes from its
	// lk.agents.usage.stt_audio_duration points, null when none joined.
	if res.Usage.STTAudioSeconds.Sum, err = s.usageSum(ctx, organisationID, projectID, callID, "lk.agents.usage.stt_audio_duration"); err != nil {
		return turn.Result{}, err
	}
	return res, nil
}

// usageSum adds up a usage counter's points joined to a call; nil when none joined.
func (s *Store) usageSum(ctx context.Context, organisationID, projectID, callID, name string) (*float64, error) {
	var (
		n   uint64
		sum float64
	)
	if err := s.conn.QueryRow(ctx, `
		SELECT count(), sum(value) FROM metric_points FINAL
		WHERE organisation_id = @org AND project_id = @project AND call_id = @call AND name = @name`,
		clickhouse.Named("org", organisationID), clickhouse.Named("project", projectID),
		clickhouse.Named("call", callID), clickhouse.Named("name", name)).Scan(&n, &sum); err != nil {
		return nil, fmt.Errorf("usage %s: %w", name, err)
	}
	if n == 0 {
		return nil, nil
	}
	return &sum, nil
}

// CallSpans returns every span that resolves to a call, by its own call id or its trace's link, in
// start order.
func (s *Store) CallSpans(ctx context.Context, organisationID, projectID, callID string) ([]store.Span, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT toString(trace_id), toString(span_id), parent_span_id, call_id, name, kind, start_time, end_time, status_code, room_id, job_id, scope_name, attributes
		FROM spans FINAL
		WHERE organisation_id = @org AND project_id = @project
		  AND (call_id = @call OR (call_id = '' AND trace_id IN (
		      SELECT trace_id FROM trace_calls FINAL WHERE organisation_id = @org AND project_id = @project AND call_id = @call)))
		ORDER BY start_time`,
		clickhouse.Named("org", organisationID), clickhouse.Named("project", projectID), clickhouse.Named("call", callID))
	if err != nil {
		return nil, fmt.Errorf("call spans: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.Span
	for rows.Next() {
		var (
			sp         store.Span
			start, end *time.Time
		)
		if err := rows.Scan(&sp.TraceID, &sp.SpanID, &sp.ParentSpanID, &sp.CallID, &sp.Name, &sp.Kind, &start, &end,
			&sp.StatusCode, &sp.RoomID, &sp.JobID, &sp.ScopeName, &sp.Attributes); err != nil {
			return nil, fmt.Errorf("call spans: %w", err)
		}
		// Null is a time the exporter did not send: zero, which turn reads as unknown.
		if start != nil {
			sp.Start = start.UTC()
		}
		if end != nil {
			sp.End = end.UTC()
		}
		out = append(out, sp)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("call spans: %w", err)
	}
	return out, nil
}

// chatHistory returns the chat_history.json of the call's latest session report, nil when it has
// none.
func (s *Store) chatHistory(ctx context.Context, organisationID, projectID, callID string) ([]byte, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT payload FROM events FINAL
		WHERE organisation_id = @org AND project_id = @project AND kind = @kind AND call_id = @call
		ORDER BY received_at DESC LIMIT 1`,
		clickhouse.Named("org", organisationID), clickhouse.Named("project", projectID),
		clickhouse.Named("kind", string(event.KindAgentSessionReport)), clickhouse.Named("call", callID))
	if err != nil {
		return nil, fmt.Errorf("session report: %w", err)
	}
	defer func() { _ = rows.Close() }()
	if !rows.Next() {
		return nil, rows.Err()
	}
	var payload string
	if err := rows.Scan(&payload); err != nil {
		return nil, fmt.Errorf("session report: %w", err)
	}
	var report event.SessionReport
	if err := json.Unmarshal([]byte(payload), &report); err != nil {
		// A report the worker stored but cannot read holds no history to pair; the turns stand.
		return nil, nil //nolint:nilerr // an unreadable report is no history, not a failed read
	}
	if report.ChatHistory == "" {
		return nil, nil
	}
	return []byte(report.ChatHistory), nil
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
