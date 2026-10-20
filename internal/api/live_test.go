package api

import (
	"bufio"
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type liveFixture struct {
	changes chan struct{}
	closed  chan struct{}
	fail    bool
	watches atomic.Int64
}

func (l *liveFixture) Watch(_ context.Context, org, project string) (<-chan struct{}, func(), error) {
	l.watches.Add(1)
	if org != "default" || project != "p" {
		return nil, nil, errors.New("wrong scope")
	}
	if l.fail {
		return nil, nil, errors.New("private redis connection detail")
	}
	return l.changes, func() { close(l.closed) }, nil
}

type liveProjects struct{}

func (liveProjects) Project(_ context.Context, org, project string) (store.Project, error) {
	if org == "default" && project == "p" {
		return store.Project{OrganisationID: org, ID: project}, nil
	}
	return store.Project{}, store.ErrNotFound
}

func liveAPI(l *liveFixture) (*API, string) {
	gate, key := KeyFor("default", "p")
	return &API{Gate: gate, Projects: liveProjects{}, Live: l, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}, key
}

func TestLiveStreamSecurityAndFailure(t *testing.T) {
	l := &liveFixture{fail: true}
	a, key := liveAPI(l)
	for _, tt := range []struct {
		project, credential string
		status              int
	}{
		{"p", "", 401}, {"other", key, 403}, {"p", key, 503},
	} {
		r := httptest.NewRequest(http.MethodGet, "/api/v1/org/default/project/"+tt.project+"/live-events", nil)
		if tt.credential != "" {
			r.Header.Set("Authorization", "Bearer "+tt.credential)
		}
		w := httptest.NewRecorder()
		a.Routes().ServeHTTP(w, r)
		if w.Code != tt.status {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}
		if strings.Contains(w.Body.String(), "private redis") {
			t.Fatal("internal detail leaked")
		}
	}
	if l.watches.Load() != 1 {
		t.Fatal("unauthorized request reached subscription")
	}
}

func TestLiveStreamRefreshDisconnectAndCleanup(t *testing.T) {
	for _, cancelClient := range []bool{false, true} {
		t.Run(map[bool]string{false: "redis disconnect", true: "client leaves"}[cancelClient], func(t *testing.T) {
			l := &liveFixture{changes: make(chan struct{}, 1), closed: make(chan struct{})}
			a, key := liveAPI(l)
			srv := httptest.NewServer(a.Routes())
			defer srv.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+"/api/v1/org/default/project/p/live-events", nil)
			if err != nil {
				t.Fatal(err)
			}
			req.Header.Set("Authorization", "Bearer "+key)
			res, err := srv.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = res.Body.Close() }()
			if res.StatusCode != 200 || res.Header.Get("Cache-Control") != "no-store" || res.Header.Get("Content-Type") != "text/event-stream" {
				t.Fatalf("headers: %v", res)
			}
			reader := bufio.NewReader(res.Body)
			frame := func() {
				t.Helper()
				for _, want := range []string{"event: refresh\n", "data: {}\n", "\n"} {
					got, err := reader.ReadString('\n')
					if err != nil || got != want {
						t.Fatalf("frame %q %v", got, err)
					}
				}
			}
			frame() // Every reconnect repairs missed notifications with an authoritative snapshot refresh.
			l.changes <- struct{}{}
			frame()
			if cancelClient {
				cancel()
			} else {
				close(l.changes)
				if _, err := reader.ReadString('\n'); err != io.EOF {
					t.Fatalf("want EOF on source loss, got %v", err)
				}
			}
			select {
			case <-l.closed:
			case <-time.After(time.Second):
				t.Fatal("subscription not closed")
			}
		})
	}
}
