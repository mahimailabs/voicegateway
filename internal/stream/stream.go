package stream

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/mahimailabs/voicegateway/internal/event"
)

// Ingest is the stream every ingested envelope is appended to.
const Ingest = "voicegateway:ingest"

// version is the field layout below. A reader refuses a layout it does not know rather than guess.
const version = "1"

// The stream entry fields.
const (
	fVersion    = "v"
	fID         = "id"
	fKind       = "kind"
	fOrg        = "org"
	fProject    = "project"
	fReceivedAt = "received_at"
	fPayload    = "payload"
)

// Producer appends envelopes. Nothing trims the stream here: an entry leaves only after the worker
// has stored it.
type Producer struct {
	rdb    *redis.Client
	stream string
}

// NewProducer appends to stream.
func NewProducer(rdb *redis.Client, stream string) *Producer {
	return &Producer{rdb: rdb, stream: stream}
}

// Append adds an envelope and returns its stream entry id.
func (p *Producer) Append(ctx context.Context, e event.Envelope) (string, error) {
	if err := e.Validate(); err != nil {
		return "", err
	}
	id, err := p.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: p.stream,
		Values: []any{
			fVersion, version,
			fID, e.ID,
			fKind, string(e.Kind),
			fOrg, e.OrganisationID,
			fProject, e.ProjectID,
			fReceivedAt, e.ReceivedAt.UTC().Format(time.RFC3339Nano),
			fPayload, e.Payload,
		},
	}).Result()
	if err != nil {
		return "", fmt.Errorf("append to %s: %w", p.stream, err)
	}
	return id, nil
}

// Message is one entry read from the stream. Err is set when the entry could not be decoded; it
// still carries its ID so the reader can dead-letter it instead of losing it.
type Message struct {
	ID       string
	Envelope event.Envelope
	Err      error
}

// Consumer reads the stream as one member of a consumer group.
type Consumer struct {
	rdb    *redis.Client
	stream string
	group  string
	name   string
}

// NewConsumer reads stream in group as consumer name.
func NewConsumer(rdb *redis.Client, stream, group, name string) *Consumer {
	return &Consumer{rdb: rdb, stream: stream, group: group, name: name}
}

// Ensure creates the group if it is missing, starting from the first entry so nothing appended
// before the worker first ran is skipped.
func (c *Consumer) Ensure(ctx context.Context) error {
	err := c.rdb.XGroupCreateMkStream(ctx, c.stream, c.group, "0").Err()
	if err != nil && !strings.HasPrefix(err.Error(), "BUSYGROUP") {
		return fmt.Errorf("create group %s on %s: %w", c.group, c.stream, err)
	}
	return nil
}

// Read returns up to count new entries, waiting up to block for the first. No entries is not an
// error. block must be positive: Redis treats 0 as waiting forever, which would hold up shutdown.
func (c *Consumer) Read(ctx context.Context, count int64, block time.Duration) ([]Message, error) {
	if block <= 0 {
		return nil, fmt.Errorf("read %s: block %s must be positive", c.stream, block)
	}
	res, err := c.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    c.group,
		Consumer: c.name,
		Streams:  []string{c.stream, ">"},
		Count:    count,
		Block:    block,
	}).Result()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", c.stream, err)
	}
	var out []Message
	for _, s := range res {
		for _, m := range s.Messages {
			e, err := decode(m.Values)
			out = append(out, Message{ID: m.ID, Envelope: e, Err: err})
		}
	}
	return out, nil
}

// Ack marks entries as stored, so the group never delivers them again.
func (c *Consumer) Ack(ctx context.Context, ids ...string) error {
	if len(ids) == 0 {
		return nil
	}
	if err := c.rdb.XAck(ctx, c.stream, c.group, ids...).Err(); err != nil {
		return fmt.Errorf("ack on %s: %w", c.stream, err)
	}
	return nil
}

func decode(v map[string]any) (event.Envelope, error) {
	get := func(k string) string {
		s, _ := v[k].(string)
		return s
	}
	if got := get(fVersion); got != version {
		return event.Envelope{}, fmt.Errorf("entry layout %q, want %q", got, version)
	}
	at, err := time.Parse(time.RFC3339Nano, get(fReceivedAt))
	if err != nil {
		return event.Envelope{}, fmt.Errorf("entry received_at: %w", err)
	}
	e := event.Envelope{
		ID:             get(fID),
		Kind:           event.Kind(get(fKind)),
		OrganisationID: get(fOrg),
		ProjectID:      get(fProject),
		ReceivedAt:     at,
		Payload:        []byte(get(fPayload)),
	}
	return e, e.Validate()
}
