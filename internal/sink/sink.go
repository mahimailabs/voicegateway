package sink

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
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
	DeadLetter(ctx context.Context, m stream.Message, reason string, scrub func([]byte) []byte) error
}

// Events is where envelopes are stored.
type Events interface {
	Existing(ctx context.Context, keys []store.EventKey) (map[store.EventKey]bool, error)
	// Insert stores every row or none. It wraps store.ErrBadRow when the rows themselves were
	// refused; any other error is transient.
	Insert(ctx context.Context, events []store.Event) error
}

// Joiner resolves rows to calls before they are stored, setting each row's call id and what is
// stored with it. It may be called again with the same rows; an error is transient.
type Joiner interface {
	Join(ctx context.Context, rows []store.Event) error
}

// Sink moves envelopes from the stream into Events.
type Sink struct {
	Stream Stream
	Events Events
	Join   Joiner
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
	// Timeout bounds each call to Events, so a hung ClickHouse is retried and logged, not waited
	// on forever.
	Timeout time.Duration
}

// Run stores entries until ctx is cancelled. It returns nil on cancellation; entries in flight stay
// pending and are claimed on the next start.
func (s *Sink) Run(ctx context.Context) error {
	// retry returns only when the group exists or ctx is cancelled.
	if s.retry(ctx, "create consumer group", s.Stream.Ensure) != nil {
		return nil
	}
	for ctx.Err() == nil {
		var msgs []stream.Message
		err := s.retry(ctx, "read stream", func(ctx context.Context) error {
			var err error
			msgs, err = s.Stream.Claim(ctx, s.ClaimIdle, s.Batch)
			if err == nil && len(msgs) == 0 {
				msgs, err = s.Stream.Read(ctx, s.Batch, s.Block)
			}
			return err
		})
		if err == nil && len(msgs) > 0 {
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
		cctx, cancel := context.WithTimeout(ctx, s.Timeout)
		defer cancel()
		stored, err := s.Events.Existing(cctx, keys)
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
	// Joined before inserting: an event row that carries a call id always has its call.
	if err := s.retry(ctx, "join calls", func(ctx context.Context) error {
		cctx, cancel := context.WithTimeout(ctx, s.Timeout)
		defer cancel()
		return s.Join.Join(cctx, fresh)
	}); err != nil {
		return
	}
	msgsByID := byID(msgs)
	for _, chunk := range chunks(fresh, maxBatchBytes) {
		if err := s.insert(ctx, chunk, msgsByID); err != nil {
			return
		}
	}
	if err := s.retry(ctx, "acknowledge", func(ctx context.Context) error { return s.Stream.Ack(ctx, ids...) }); err != nil {
		return
	}
	s.Log.Debug("sink: stored batch", "entries", len(msgs), "inserted", len(fresh))
}

// maxBatchBytes caps the payload sent in one insert, so a burst of large uploads does not make a
// batch ClickHouse cannot take in one call.
const maxBatchBytes = 32 << 20

// splitAfter is how many transient failures in a row a batch of several rows takes before it is
// halved, in case its size is what fails (a memory limit, the call timeout).
const splitAfter = 3

// chunks splits rows into runs of at most limit payload bytes; a row larger than limit is a run of
// its own.
func chunks(rows []store.Event, limit int) [][]store.Event {
	var out [][]store.Event
	start, size := 0, 0
	for i, r := range rows {
		if i > start && size+len(r.Payload) > limit {
			out = append(out, rows[start:i])
			start, size = i, 0
		}
		size += len(r.Payload)
	}
	if start < len(rows) {
		out = append(out, rows[start:])
	}
	return out
}

// insert stores rows. A transient failure, such as ClickHouse down, busy or slow, is retried
// without end and nothing is acknowledged meanwhile; after splitAfter of them in a row the batch is
// halved, since its size may be the cause, and the halves are retried the same way. When
// ClickHouse refuses the rows themselves, the batch is halved until the refused rows are found;
// only those are dead-lettered.
func (s *Sink) insert(ctx context.Context, rows []store.Event, msgs map[string]stream.Message) error {
	if len(rows) == 0 {
		return nil
	}
	wait := s.Backoff
	for failures := 1; ; failures++ {
		cctx, cancel := context.WithTimeout(ctx, s.Timeout)
		err := s.Events.Insert(cctx, rows)
		cancel()
		switch {
		case err == nil:
			return nil
		case ctx.Err() != nil:
			return ctx.Err()
		case errors.Is(err, store.ErrBadRow) && len(rows) == 1:
			s.deadLetter(ctx, msgs[rows[0].StreamID], "insert: "+err.Error())
			return ctx.Err()
		case errors.Is(err, store.ErrBadRow), failures >= splitAfter && len(rows) > 1:
			if !errors.Is(err, store.ErrBadRow) {
				s.Log.Error("sink: insert keeps failing, halving the batch", "rows", len(rows), "err", err)
			}
			half := len(rows) / 2
			if err := s.insert(ctx, rows[:half], msgs); err != nil {
				return err
			}
			return s.insert(ctx, rows[half:], msgs)
		}
		s.Log.Error("sink: insert failed, keeping entries pending", "rows", len(rows), "err", err)
		s.sleep(ctx, wait)
		wait = min(wait*2, s.MaxBackoff)
	}
}

func byID(msgs []stream.Message) map[string]stream.Message {
	out := make(map[string]stream.Message, len(msgs))
	for _, m := range msgs {
		out[m.ID] = m
	}
	return out
}

// deadLetter moves an entry to the dead-letter stream with any TURN password blanked, since the
// body there is not the stored, stripped one.
func (s *Sink) deadLetter(ctx context.Context, m stream.Message, reason string) {
	s.Log.Warn("sink: dead-lettering entry", "entry", m.ID, "reason", reason)
	_ = s.retry(ctx, "dead-letter", func(ctx context.Context) error {
		return s.Stream.DeadLetter(ctx, m, reason, blankTURNPassword)
	})
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

// turnPasswordValue matches the value of any "turnPassword" member, however the body around it is
// broken.
var turnPasswordValue = regexp.MustCompile(`("turnPassword"\s*:\s*)"(?:[^"\\]|\\.)*"`)

// blankTURNPassword empties every turnPassword value without parsing the body, for bodies that may
// not parse.
func blankTURNPassword(body []byte) []byte {
	return turnPasswordValue.ReplaceAll(body, []byte(`$1""`))
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
