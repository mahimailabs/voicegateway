package chstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// A layer poller's insert is asynchronous on the server but returns only once its rows are written:
// a read is readable, with its samples, as soon as StoreRead returns.
func TestStoreReadIsReadableOnReturn(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	at := time.Now().UTC().Truncate(time.Millisecond)
	read := store.LayerRead{OrganisationID: "default", ProjectID: "production", Layer: store.LayerMediaServer, ReadAt: at, OK: true, Every: 5 * time.Second}
	room := store.RoomSample{OrganisationID: "default", ProjectID: "production", ReadAt: at, Room: store.Room{SID: "RM_one"}}
	if err := s.StoreRead(ctx, read, []store.RoomSample{room}, nil); err != nil {
		t.Fatal(err)
	}

	reads, err := s.LayerReads(ctx, "default", "production", store.LayerMediaServer, at.Add(-time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if len(reads) != 1 || !reads[0].ReadAt.Equal(at) || !reads[0].OK {
		t.Fatalf("LayerReads = %+v, want the one good read at %s", reads, at)
	}
	var rooms uint64
	if err := s.conn.QueryRow(ctx, "SELECT count() FROM room_samples WHERE room_sid = 'RM_one'").Scan(&rooms); err != nil {
		t.Fatal(err)
	}
	if rooms != 1 {
		t.Errorf("room_samples holds %d rows, want 1", rooms)
	}

	if err := s.conn.Exec(ctx, "SYSTEM FLUSH LOGS"); err != nil {
		t.Fatal(err)
	}
	var async uint64
	if err := s.conn.QueryRow(ctx, `SELECT count() FROM system.query_log
		WHERE type = 'QueryFinish' AND query_kind = 'AsyncInsertFlush' AND has(tables, currentDatabase() || '.layer_reads')`).Scan(&async); err != nil {
		t.Fatal(err)
	}
	if async == 0 {
		t.Error("layer_reads was not written by an asynchronous insert flush")
	}
}
