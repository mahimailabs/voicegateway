package app

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/livekit/protocol/auth"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

type scopedIngestionStore struct{}

func (scopedIngestionStore) Project(_ context.Context, org, id string) (store.Project, error) {
	if id != "voice" || (org != "customer" && org != "other") {
		return store.Project{}, store.ErrNotFound
	}
	return store.Project{OrganisationID: org, ID: id, LiveKitAPIKey: "same-key"}, nil
}

func TestScopedWebhookAndSessionAuthentication(t *testing.T) {
	body := `{"event":"room_started","id":"event-1"}`
	sum := sha256.Sum256([]byte(body))
	webhookToken, err := auth.NewAccessToken("same-key", "customer-secret").SetSha256(base64.StdEncoding.EncodeToString(sum[:])).ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	uploadToken, err := auth.NewAccessToken("same-key", "customer-secret").SetObservabilityGrant(&auth.ObservabilityGrant{Write: true}).ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name, path, token, body string
		status                  int
		appended                int
	}{
		{"other webhook", "/ingest/other/voice/livekit/webhook", webhookToken, body, 401, 0},
		{"changed webhook", "/ingest/customer/voice/livekit/webhook", webhookToken, body + " ", 401, 0},
		{"valid webhook", "/ingest/customer/voice/livekit/webhook", webhookToken, body, 200, 1},
		{"other session", "/ingest/other/voice/observability/recordings/v0", "Bearer " + uploadToken, "", 401, 0},
		{"unscoped session", "/observability/recordings/v0", "Bearer " + uploadToken, "", 404, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			stream := &scopedIngestionStream{}
			mux := http.NewServeMux()
			mountIngestion(mux, scopedIngestionStore{}, stream, nil, slog.New(slog.NewTextHandler(io.Discard, nil)), time.Now)
			req := httptest.NewRequest(http.MethodPost, tc.path, strings.NewReader(tc.body))
			req.Header.Set("Authorization", tc.token)
			rec := httptest.NewRecorder()
			mux.ServeHTTP(rec, req)
			if rec.Code != tc.status || len(stream.events) != tc.appended {
				t.Fatalf("status %d, appended %d", rec.Code, len(stream.events))
			}
			if tc.appended > 0 && stream.events[0].OrganisationID != "customer" {
				t.Fatal("wrong webhook destination")
			}
		})
	}
}

func (scopedIngestionStore) LiveKitSecret(_ context.Context, org, _ string) (string, error) {
	return org + "-secret", nil
}

type scopedIngestionStream struct{ events []event.Envelope }

type deniedScopedQuota struct{ checked []string }

func (q *deniedScopedQuota) Check(_ context.Context, org string) (bool, int, error) {
	q.checked = append(q.checked, org)
	return false, 100, nil
}

func TestScopedQuotaRunsOnlyAfterAuthentication(t *testing.T) {
	for _, kind := range []string{"traces", "logs", "metrics"} {
		t.Run(kind, func(t *testing.T) {
			q := &deniedScopedQuota{}
			stream := &scopedIngestionStream{}
			mux := http.NewServeMux()
			mountIngestion(mux, scopedIngestionStore{}, stream, q, slog.New(slog.NewTextHandler(io.Discard, nil)), time.Now)
			token, err := auth.NewAccessToken("same-key", "customer-secret").SetObservabilityGrant(&auth.ObservabilityGrant{Write: true}).ToJWT()
			if err != nil {
				t.Fatal(err)
			}
			for _, tc := range []struct {
				org    string
				status int
			}{
				{"other", http.StatusUnauthorized},
				{"customer", http.StatusPaymentRequired},
			} {
				req := httptest.NewRequest(http.MethodPost, "/ingest/"+tc.org+"/voice/observability/"+kind+"/otlp/v0", strings.NewReader("\x0a\x00"))
				req.Header.Set("Authorization", "Bearer "+token)
				rec := httptest.NewRecorder()
				mux.ServeHTTP(rec, req)
				if rec.Code != tc.status {
					t.Fatalf("%s: status %d", tc.org, rec.Code)
				}
			}
			if len(q.checked) != 1 || q.checked[0] != "customer" || len(stream.events) != 0 {
				t.Fatalf("quota calls %v; appended %d", q.checked, len(stream.events))
			}
		})
	}
}

func (s *scopedIngestionStream) Append(_ context.Context, e event.Envelope) (string, error) {
	s.events = append(s.events, e)
	return "1-0", nil
}

func TestScopedIngestionRoutes(t *testing.T) {
	for _, kind := range []string{"traces", "logs", "metrics"} {
		t.Run(kind, func(t *testing.T) {
			stream := &scopedIngestionStream{}
			mux := http.NewServeMux()
			mountIngestion(mux, scopedIngestionStore{}, stream, nil, slog.New(slog.NewTextHandler(io.Discard, nil)), time.Now)
			token, err := auth.NewAccessToken("same-key", "customer-secret").SetObservabilityGrant(&auth.ObservabilityGrant{Write: true}).ToJWT()
			if err != nil {
				t.Fatal(err)
			}
			path := "/observability/" + kind + "/otlp/v0"
			for _, tc := range []struct {
				prefix string
				status int
			}{
				{"/ingest/other/voice", http.StatusUnauthorized},
				{"/ingest/missing/voice", http.StatusUnauthorized},
				{"", http.StatusNotFound},
				{"/ingest/customer/voice", http.StatusOK},
			} {
				req := httptest.NewRequest(http.MethodPost, tc.prefix+path, strings.NewReader("\x0a\x00"))
				req.Header.Set("Authorization", "Bearer "+token)
				rec := httptest.NewRecorder()
				mux.ServeHTTP(rec, req)
				if rec.Code != tc.status {
					t.Fatalf("%s: got %d, want %d", tc.prefix, rec.Code, tc.status)
				}
				if tc.status != http.StatusOK && len(stream.events) != 0 {
					t.Fatal("rejected request appended data")
				}
			}
			if len(stream.events) != 1 || stream.events[0].OrganisationID != "customer" {
				t.Fatal("wrong attribution")
			}
		})
	}
}
