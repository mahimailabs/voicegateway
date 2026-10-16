package sink

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// Stream is the consumer-group side of the ingest stream.
type Stream interface {
	Ensure(ctx context.Context) error
	Read(ctx context.Context, count int64, block time.Duration) ([]stream.Message, error)
	Claim(ctx context.Context, minIdle time.Duration, count int64) ([]stream.Message, error)
	Ack(ctx context.Context, ids ...string) error
	DeadLetter(ctx context.Context, m stream.Message, reason string) error
}

// Events is where envelopes are stored.
type Events interface {
	Existing(ctx context.Context, keys []store.EventKey) (map[store.EventKey]bool, error)
	Insert(ctx context.Context, events []store.Event) error
	Ping(ctx context.Context) error
}

// Sink moves envelopes from the stream into Events.
type Sink struct {
	Stream Stream
	Events Events
	Log    *slog.Logger
	// Batch is the most entries read and stored at once.
	Batch int64
	// Block is how long a read waits for new entries; it must be positive.
	Block time.Duration
	// ClaimIdle is how long an entry stays unacknowledged with another consumer before this one
	// takes it over.
	ClaimIdle time.Duration
	// Backoff is the first wait after a failure; it doubles up to MaxBackoff.
	Backoff, MaxBackoff time.Duration
	// Attempts is how many times a batch is tried while ClickHouse answers before its rows are
	// stored one by one and the ones that still fail are dead-lettered.
	Attempts int
}

// Run stores entries until ctx is cancelled. It returns nil on cancellation; entries in flight stay
// pending and are claimed on the next start.
func (s *Sink) Run(ctx context.Context) error {
	// retry returns only when the group exists or ctx is cancelled.
	if s.retry(ctx, "create consumer group", s.Stream.Ensure) != nil {
		return nil
	}
	for ctx.Err() == nil {
		msgs, err := s.Stream.Claim(ctx, s.ClaimIdle, s.Batch)
		if err == nil && len(msgs) == 0 {
			msgs, err = s.Stream.Read(ctx, s.Batch, s.Block)
		}
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			s.Log.Error("sink: read stream", "err", err)
			s.sleep(ctx, s.Backoff)
			continue
		}
		if len(msgs) > 0 {
			s.store(ctx, msgs)
		}
	}
	return nil
}

// store stores one batch and acknowledges it. It returns early, leaving entries pending, only when
// ctx is cancelled.
func (s *Sink) store(ctx context.Context, msgs []stream.Message) {
	var (
		rows []store.Event
		ids  []string
	)
	for _, m := range msgs {
		if m.Err != nil {
			s.deadLetter(ctx, m, "decode: "+m.Err.Error())
			continue
		}
		row, err := toEvent(m)
		if err != nil {
			s.deadLetter(ctx, m, err.Error())
			continue
		}
		rows = append(rows, row)
		ids = append(ids, m.ID)
	}
	if len(rows) == 0 {
		return
	}

	var fresh []store.Event
	err := s.retry(ctx, "find stored events", func(ctx context.Context) error {
		keys := make([]store.EventKey, len(rows))
		for i, r := range rows {
			keys[i] = r.Key()
		}
		stored, err := s.Events.Existing(ctx, keys)
		if err != nil {
			return err
		}
		fresh = fresh[:0]
		seen := map[store.EventKey]bool{}
		for _, r := range rows {
			if stored[r.Key()] || seen[r.Key()] {
				continue
			}
			seen[r.Key()] = true
			fresh = append(fresh, r)
		}
		return nil
	})
	if err != nil {
		return
	}
	if skipped := len(rows) - len(fresh); skipped > 0 {
		s.Log.Info("sink: skipped events already stored", "count", skipped)
	}
	if err := s.insert(ctx, fresh, msgs); err != nil {
		return
	}
	if err := s.retry(ctx, "acknowledge", func(ctx context.Context) error { return s.Stream.Ack(ctx, ids...) }); err != nil {
		return
	}
	s.Log.Debug("sink: stored batch", "entries", len(msgs), "inserted", len(fresh))
}

// insert stores rows, retrying while ClickHouse is unreachable. When ClickHouse answers but the
// batch keeps failing, rows are stored one by one and those that still fail are dead-lettered.
func (s *Sink) insert(ctx context.Context, rows []store.Event, msgs []stream.Message) error {
	if len(rows) == 0 {
		return nil
	}
	failures := 0
	wait := s.Backoff
	for {
		err := s.Events.Insert(ctx, rows)
		if err == nil {
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if s.Events.Ping(ctx) != nil {
			s.Log.Error("sink: ClickHouse unreachable, keeping entries pending", "rows", len(rows), "err", err)
		} else {
			failures++
			s.Log.Error("sink: insert failed", "rows", len(rows), "attempt", failures, "err", err)
			if failures >= s.Attempts {
				return s.insertEach(ctx, rows, msgs)
			}
		}
		s.sleep(ctx, wait)
		wait = min(wait*2, s.MaxBackoff)
	}
}

func (s *Sink) insertEach(ctx context.Context, rows []store.Event, msgs []stream.Message) error {
	byID := map[string]stream.Message{}
	for _, m := range msgs {
		byID[m.ID] = m
	}
	for _, r := range rows {
		if err := s.Events.Insert(ctx, []store.Event{r}); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			s.deadLetter(ctx, byID[r.StreamID], "insert: "+err.Error())
		}
	}
	return nil
}

func (s *Sink) deadLetter(ctx context.Context, m stream.Message, reason string) {
	s.Log.Warn("sink: dead-lettering entry", "entry", m.ID, "reason", reason)
	_ = s.retry(ctx, "dead-letter", func(ctx context.Context) error { return s.Stream.DeadLetter(ctx, m, reason) })
}

// retry runs f until it succeeds or ctx is cancelled, backing off between attempts.
func (s *Sink) retry(ctx context.Context, what string, f func(context.Context) error) error {
	wait := s.Backoff
	for {
		err := f(ctx)
		if err == nil {
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		s.Log.Error("sink: "+what, "err", err)
		s.sleep(ctx, wait)
		wait = min(wait*2, s.MaxBackoff)
	}
}

func (s *Sink) sleep(ctx context.Context, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
	case <-t.C:
	}
}

// toEvent turns an envelope into its row. The dedupe key is the source's event id, else the entry
// id, so a redelivered entry collapses onto the row already stored.
func toEvent(m stream.Message) (store.Event, error) {
	e := m.Envelope
	payload := e.Payload
	if e.Kind == event.KindLiveKitWebhook {
		var err error
		if payload, err = withoutTURNPassword(payload); err != nil {
			return store.Event{}, err
		}
	}
	key := e.ID
	if key == "" {
		key = "entry:" + m.ID
	}
	return store.Event{
		OrganisationID: e.OrganisationID,
		ProjectID:      e.ProjectID,
		Kind:           string(e.Kind),
		DedupeKey:      key,
		EventID:        e.ID,
		StreamID:       m.ID,
		ReceivedAt:     e.ReceivedAt,
		Payload:        payload,
	}, nil
}

// withoutTURNPassword removes room.turnPassword from a webhook body. VoiceGateway never needs it,
// so it is not kept (maintainer decision, 2026-10-16). A body without one is returned unchanged.
func withoutTURNPassword(body []byte) ([]byte, error) {
	if !bytes.Contains(body, []byte(`"turnPassword"`)) {
		return body, nil
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var doc map[string]any
	if err := dec.Decode(&doc); err != nil {
		return nil, fmt.Errorf("webhook body: %w", err)
	}
	if room, ok := doc["room"].(map[string]any); ok {
		delete(room, "turnPassword")
	}
	var out bytes.Buffer
	enc := json.NewEncoder(&out)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(doc); err != nil {
		return nil, fmt.Errorf("webhook body: %w", err)
	}
	return bytes.TrimSuffix(out.Bytes(), []byte("\n")), nil
}
