package pgstore

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"io"
	"log/slog"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/livekit/protocol/auth"

	"github.com/mahimailabs/voicegateway/ee/instance"
	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/ingest"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/store"
)

type quotaJoinAppender struct{ joiner *join.Joiner }

func (a quotaJoinAppender) Append(ctx context.Context, e event.Envelope) (string, error) {
	return "test", a.joiner.Join(ctx, []store.Event{{OrganisationID: e.OrganisationID, ProjectID: e.ProjectID, Kind: string(e.Kind), Payload: e.Payload, ReceivedAt: e.ReceivedAt}})
}

func TestQuotaAcceptsKnownCallFinishAndRefusesNewCall(t *testing.T) {
	s, pool := quotaDatabase(t)
	ctx := t.Context()
	if _, err := s.CreateProject(ctx, project("continuation", "quota-key")); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 10, 19, 1, 0, 0, 0, time.UTC)
	start := now.Add(-time.Hour)
	entitlement := store.Entitlement{From: start, To: start.Add(30 * 24 * time.Hour), LimitMinutes: 1, Active: true, Revision: 1}
	if err := s.PutEntitlement(ctx, "default", entitlement); err != nil {
		t.Fatal(err)
	}
	// This call was indexed before a different call consumed the remaining allowance.
	id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "continuation", RoomSID: "RM_existing", StartedAt: &start, FirstSeenAt: &start, LastActivityAt: &start})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, "INSERT INTO calls(organisation_id,project_id,started_at,ended_at,end_state) VALUES('default','continuation',$1,$2,'observed')", start, start.Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	quota := &instance.Quota{Store: s, Now: func() time.Time { return now }}
	handler := &ingest.Webhooks{Quota: quota, Projects: s, Stream: quotaJoinAppender{&join.Joiner{Calls: s, Log: logger}}, Log: logger, Now: func() time.Time { return now }}
	send := func(room, kind string) int {
		body := fmt.Sprintf(`{"event":%q,"room":{"sid":%q,"name":"call"},"createdAt":%d}`, kind, room, now.Unix())
		sum := sha256.Sum256([]byte(body))
		token, err := auth.NewAccessToken("quota-key", "lk-secret-continuation").SetValidFor(time.Minute).SetSha256(base64.StdEncoding.EncodeToString(sum[:])).ToJWT()
		if err != nil {
			t.Fatal(err)
		}
		r := httptest.NewRequest("POST", "/livekit/webhook", strings.NewReader(body))
		r.Header.Set("Authorization", token)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w.Code
	}
	if status := send("RM_new", "room_started"); status != 402 {
		t.Fatalf("new call: %d", status)
	}
	if status := send("RM_existing", "room_finished"); status != 200 {
		t.Fatalf("existing finish: %d", status)
	}
	var state string
	if err := pool.QueryRow(ctx, "SELECT end_state FROM calls WHERE id=$1", id).Scan(&state); err != nil || state != "observed" {
		t.Fatalf("end state %s: %v", state, err)
	}
	usage, err := s.EntitlementUsage(ctx, "default")
	if err != nil || usage.CallSeconds != 3660 {
		t.Fatalf("finished usage %+v: %v", usage, err)
	}
	refs := []event.CallIdentity{{RoomSID: "RM_existing"}}
	if ok, err := s.KnownQuotaCalls(ctx, "default", "other-project", refs, entitlement.From, entitlement.To); err != nil || ok {
		t.Fatalf("cross project: %v %v", ok, err)
	}
	refs = append(refs, event.CallIdentity{RoomSID: "RM_new"})
	if ok, err := s.KnownQuotaCalls(ctx, "default", "continuation", refs, entitlement.From, entitlement.To); err != nil || ok {
		t.Fatalf("mixed calls: %v %v", ok, err)
	}
	entitlement.Revision = 2
	entitlement.Active = false
	if err := s.PutEntitlement(ctx, "default", entitlement); err != nil {
		t.Fatal(err)
	}
	if status := send("RM_existing", "room_finished"); status != 402 {
		t.Fatalf("revoked: %d", status)
	}
}
