package chstore

import (
	"context"
	"io/fs"
	"os"
	"testing"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/pressly/goose/v3"
	"github.com/testcontainers/testcontainers-go"
	tcclickhouse "github.com/testcontainers/testcontainers-go/modules/clickhouse"

	"github.com/mahimailabs/voicegateway/migrations"
)

// The same image, tag and digest as deploy/compose.yaml.
const clickhouseImage = "clickhouse/clickhouse-server:26.1.12.23-alpine@sha256:369bd68942569e0effdf0aa68b4f844a74d97b73e281cf8012ce90e9865cc411"

// Acceptance check 11 of phase 04a: on the phase 03 schema with stored events, migrate adds the
// call tables and the call_id column, keeps the rows, and a second run applies nothing.
func TestMigrateTwice(t *testing.T) {
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()
	ch, err := tcclickhouse.Run(ctx, clickhouseImage,
		tcclickhouse.WithDatabase("voicegateway"), tcclickhouse.WithUsername("voicegateway"), tcclickhouse.WithPassword("ch-test"))
	testcontainers.CleanupContainer(t, ch)
	if err != nil {
		t.Fatalf("start clickhouse: %v", err)
	}
	url, err := ch.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	opts, err := clickhouse.ParseDSN(url)
	if err != nil {
		t.Fatal(err)
	}
	db := clickhouse.OpenDB(opts)
	t.Cleanup(func() { _ = db.Close() })

	// The phase 03 schema, holding one event.
	fsys, err := fs.Sub(migrations.ClickHouse, "clickhouse")
	if err != nil {
		t.Fatal(err)
	}
	provider, err := goose.NewProvider(goose.DialectClickHouse, db, fsys)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := provider.UpTo(ctx, 1); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `INSERT INTO events (organisation_id, project_id, kind, dedupe_key, event_id, stream_id, received_at, payload)
		VALUES ('default', 'production', 'livekit.webhook', 'EV_1', 'EV_1', '1-0', now64(9), '{}')`); err != nil {
		t.Fatal(err)
	}

	if n, err := Migrate(ctx, url); err != nil || n == 0 {
		t.Fatalf("first migrate = %d, %v; want some applied", n, err)
	}
	if n, err := Migrate(ctx, url); err != nil || n != 0 {
		t.Fatalf("second migrate = %d, %v; want 0", n, err)
	}
	var callID string
	if err := db.QueryRowContext(ctx, `SELECT call_id FROM events WHERE dedupe_key = 'EV_1'`).Scan(&callID); err != nil || callID != "" {
		t.Fatalf("stored event call_id = %q, %v; want the row kept with an empty call id", callID, err)
	}
	for _, q := range []string{"SELECT count() FROM spans", "SELECT count() FROM trace_calls", "SELECT count() FROM unmatched"} {
		if _, err := db.ExecContext(ctx, q); err != nil {
			t.Errorf("%s: %v", q, err)
		}
	}
}
