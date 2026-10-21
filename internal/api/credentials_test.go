package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type oneProject struct{}

func (oneProject) Project(_ context.Context, org, id string) (store.Project, error) {
	if org != "acme" || id != "voice" {
		return store.Project{}, store.ErrNotFound
	}
	return store.Project{OrganisationID: org, ID: id, LiveKitURL: "wss://lk.example.com", LiveKitAPIKey: "APIold"}, nil
}

type recordCredentials struct {
	calls int
	key   string
}

func (c *recordCredentials) UpdateLiveKitCredentials(_ context.Context, _, _, key, _ string) error {
	c.calls++
	c.key = key
	return nil
}

// verifyAt records the URL it was asked to verify against.
type verifyAt struct {
	err error
	url *string
}

func (v verifyAt) VerifyLiveKit(_ context.Context, url, _, _ string) error {
	*v.url = url
	return v.err
}

// updateProjectLiveKitCredentials: input rules, the verifier against the stored URL, an unknown
// project, and that neither the cause nor the secret is ever answered.
func TestUpdateLiveKitCredentials(t *testing.T) {
	cause := errors.New("dial tcp 10.0.0.7:443: prohibited IP address")
	valid := `{"livekitApiKey":" APInew ","livekitApiSecret":"new-secret"}`
	for _, tt := range []struct {
		name, project, body string
		err                 error
		status              int
		code                string
		stores              int
	}{
		{"verified", "voice", valid, nil, http.StatusNoContent, "", 1},
		{"secret refused", "voice", valid, fmt.Errorf("%w: %w", store.ErrLiveKitSecretInvalid, cause), http.StatusUnprocessableEntity, "livekit_secret_invalid", 0},
		{"no LiveKit answer", "voice", valid, fmt.Errorf("%w: %w", store.ErrLiveKitUnreachable, cause), http.StatusUnprocessableEntity, "livekit_unreachable", 0},
		{"unknown project", "nope", valid, nil, http.StatusNotFound, "not_found", 0},
		{"missing secret", "voice", `{"livekitApiKey":"APInew","livekitApiSecret":"  "}`, nil, http.StatusBadRequest, "invalid_request", 0},
		{"key with a space", "voice", `{"livekitApiKey":"API new","livekitApiSecret":"s"}`, nil, http.StatusBadRequest, "invalid_request", 0},
		{"unknown field", "voice", `{"livekitApiKey":"APInew","livekitApiSecret":"s","livekitUrl":"wss://other"}`, nil, http.StatusBadRequest, "invalid_request", 0},
		{"trailing body", "voice", valid + `{}`, nil, http.StatusBadRequest, "invalid_request", 0},
	} {
		t.Run(tt.name, func(t *testing.T) {
			creds := &recordCredentials{}
			var url string
			a := &API{Projects: oneProject{}, Credentials: creds, VerifyLiveKit: verifyAt{tt.err, &url}}
			req := httptest.NewRequest(http.MethodPut, "/", strings.NewReader(tt.body))
			rc := chi.NewRouteContext()
			rc.URLParams.Add("orgId", "acme")
			rc.URLParams.Add("projectId", tt.project)
			req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rc))
			w := httptest.NewRecorder()
			a.updateLiveKitCredentials(w, req)
			var e struct{ Code string }
			_ = json.Unmarshal(w.Body.Bytes(), &e)
			if w.Code != tt.status || e.Code != tt.code || creds.calls != tt.stores {
				t.Fatalf("= %d %s, %d stores; want %d %s, %d stores", w.Code, w.Body, creds.calls, tt.status, tt.code, tt.stores)
			}
			if w.Header().Get("Cache-Control") != "no-store" {
				t.Error("answer is cacheable")
			}
			if strings.Contains(w.Body.String(), "10.0.0.7") || strings.Contains(w.Body.String(), "new-secret") {
				t.Errorf("the answer holds the cause or the secret: %s", w.Body)
			}
			if tt.stores == 1 && (creds.key != "APInew" || url != "wss://lk.example.com") {
				t.Errorf("stored key %q verified at %q; want the trimmed key, checked at the project's own URL", creds.key, url)
			}
		})
	}
	t.Run("off without a store", func(t *testing.T) {
		w := httptest.NewRecorder()
		(&API{Projects: oneProject{}}).updateLiveKitCredentials(w, httptest.NewRequest(http.MethodPut, "/", strings.NewReader(valid)))
		if w.Code != http.StatusForbidden {
			t.Fatalf("status %d", w.Code)
		}
	})
}
