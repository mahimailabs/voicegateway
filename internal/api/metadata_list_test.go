package api

import (
	"context"
	"net/http"
	"net/url"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestMetadataFiltersDetailAndSnapshotInvalidation(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	start := time.Now().UTC()
	ids := []string{}
	for _, sid := range []string{"RM_first", "RM_second"} {
		id, err := f.pg.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: f.project, RoomSID: sid, RoomName: "shared-name", StartedAt: &start})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, id)
	}
	before, code := f.list(url.Values{"limit": {"1"}})
	if code != http.StatusOK || before.Page.NextCursor == nil {
		t.Fatalf("first page %d %+v", code, before)
	}
	if _, err := f.pg.UpdateCallMetadata(ctx, "default", f.project, ids[0], 0, []string{"support"}, true); err != nil {
		t.Fatal(err)
	}
	if _, code := f.list(url.Values{"snapshot": {before.Page.Snapshot}, "cursor": {*before.Page.NextCursor}, "limit": {"1"}}); code != http.StatusBadRequest {
		t.Fatalf("stale snapshot %d", code)
	}
	for _, q := range []url.Values{{"tag": {"support"}}, {"archived": {"true"}}, {"tag": {"missing", "support"}, "archived": {"true"}}} {
		page, code := f.list(q)
		if code != http.StatusOK || len(page.Items) != 1 || page.Items[0].ID != ids[0] || !page.Items[0].Archived || len(page.Items[0].Tags) != 1 || (page.Totals.CallCount == nil || *page.Totals.CallCount != 1) {
			t.Fatalf("filtered %v: %d %+v", q, code, page)
		}
	}
	var detail call
	if code := f.get(f.api(nil), path(f.project, ids[0]), &detail); code != http.StatusOK || !detail.Archived || len(detail.Tags) != 1 {
		t.Fatalf("detail %d %+v", code, detail)
	}
	page, code := f.list(url.Values{"archived": {"false"}})
	if code != http.StatusOK || len(page.Items) != 1 || page.Items[0].ID != ids[1] {
		t.Fatalf("unarchived %d %+v", code, page)
	}
}
