package ingest

import (
	"context"
	"encoding/json"
	"io"
	"io/fs"
	"log/slog"
	"net/http"
	"path"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/sdk/contract"
)

// sdkProjects is one project whose key is testKey, as ScopedProjects resolves it.
type sdkProjects struct{}

func (sdkProjects) ProjectByLiveKitKey(_ context.Context, key string) (store.Project, error) {
	if key != testKey {
		return store.Project{}, store.ErrNotFound
	}
	return store.Project{OrganisationID: "acme", ID: "voice", LiveKitAPIKey: testKey}, nil
}

func (sdkProjects) LiveKitSecret(context.Context, string, string) (string, error) {
	return testSecret, nil
}

// memStream keeps what was appended, one envelope per event id, as the sink's dedupe would.
type memStream struct{ byID map[string]event.Envelope }

func (m *memStream) Append(_ context.Context, e event.Envelope) (string, error) {
	if m.byID == nil {
		m.byID = map[string]event.Envelope{}
	}
	m.byID[e.ID] = e
	return "1-0", nil
}

type capQuota struct{}

func (capQuota) Check(context.Context, string) (bool, int, error) { return false, 100, nil }

func examples(t *testing.T, dir string) map[string][]byte {
	t.Helper()
	out := map[string][]byte{}
	err := fs.WalkDir(contract.Examples, "examples/"+dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		b, err := fs.ReadFile(contract.Examples, p)
		out[path.Base(p)] = b
		return err
	})
	if err != nil || len(out) == 0 {
		t.Fatalf("examples/%s: %v (%d files)", dir, err, len(out))
	}
	return out
}

// TestSdkBatches: every valid example of the wire contract is accepted and appended once, under
// its batch id, gzipped or not; every invalid one is 400 and appends nothing; a quota at its cap
// is 402 (docs/specs/python-sdk.md check 4).
func TestSdkBatches(t *testing.T) {
	newUploads := func(q Quota) (*Uploads, *memStream) {
		s := &memStream{}
		return &Uploads{Quota: q, Projects: sdkProjects{}, Stream: s, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}, s
	}
	authz := bearer(t, testKey, testSecret, true)

	for name, body := range examples(t, "valid") {
		t.Run("valid/"+name, func(t *testing.T) {
			u, s := newUploads(nil)
			h := http.HandlerFunc(u.SDKEvents)
			for _, enc := range []string{"", "gzip"} {
				b := body
				if enc == "gzip" {
					b = gzipped(t, body)
				}
				if rec := send(h, authz, enc, "application/json", b); rec.Code != http.StatusAccepted {
					t.Fatalf("%q: status %d: %s", enc, rec.Code, rec.Body)
				}
			}
			var batch event.SDKBatch
			if err := json.Unmarshal(body, &batch); err != nil {
				t.Fatal(err)
			}
			e, ok := s.byID["sdk:"+batch.BatchID]
			if len(s.byID) != 1 || !ok || e.Kind != event.KindSDKEvents || e.ProjectID != "voice" || string(e.Payload) != string(body) {
				t.Fatalf("appended %d envelopes; want one under the batch id, body unchanged", len(s.byID))
			}
		})
	}
	for name, body := range examples(t, "invalid") {
		t.Run("invalid/"+name, func(t *testing.T) {
			u, s := newUploads(nil)
			if rec := send(http.HandlerFunc(u.SDKEvents), authz, "", "application/json", body); rec.Code != http.StatusBadRequest {
				t.Fatalf("status %d, want 400", rec.Code)
			}
			if len(s.byID) != 0 {
				t.Fatalf("appended %d", len(s.byID))
			}
		})
	}
	t.Run("not JSON", func(t *testing.T) {
		u, s := newUploads(nil)
		if rec := send(http.HandlerFunc(u.SDKEvents), authz, "", "application/json", []byte("{")); rec.Code != http.StatusBadRequest || len(s.byID) != 0 {
			t.Fatalf("status %d, appended %d", rec.Code, len(s.byID))
		}
	})
	t.Run("quota at its cap", func(t *testing.T) {
		u, s := newUploads(capQuota{})
		body := examples(t, "valid")["full-call.json"]
		if rec := send(http.HandlerFunc(u.SDKEvents), authz, "", "application/json", body); rec.Code != http.StatusPaymentRequired || len(s.byID) != 0 {
			t.Fatalf("status %d, appended %d", rec.Code, len(s.byID))
		}
	})
	t.Run("another key", func(t *testing.T) {
		u, s := newUploads(nil)
		body := examples(t, "valid")["full-call.json"]
		if rec := send(http.HandlerFunc(u.SDKEvents), bearer(t, "APIother", testSecret, true), "", "application/json", body); rec.Code != http.StatusUnauthorized || len(s.byID) != 0 {
			t.Fatalf("status %d, appended %d", rec.Code, len(s.byID))
		}
	})
}
