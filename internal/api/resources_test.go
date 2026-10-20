package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type resourceFixture struct {
	now   time.Time
	fail  bool
	reads int
}

func (f *resourceFixture) ResourceEndpoints(_ context.Context, org, project string) ([]store.ResourceEndpoint, error) {
	f.reads++
	if org != "default" || project != "p" {
		return nil, errors.New("wrong scope")
	}
	return []store.ResourceEndpoint{{ID: "re_one", Host: "https://exporter.example", Kind: "worker", Target: "worker-a", ContainerID: "/docker/a"}}, nil
}

func (f *resourceFixture) ResourceSamples(_ context.Context, org, project string, _ time.Time, _ int) ([]store.ResourceSample, error) {
	if org != "default" || project != "p" {
		return nil, errors.New("wrong scope")
	}
	if f.fail {
		return nil, errors.New("private database detail")
	}
	return []store.ResourceSample{{EndpointID: "re_one", ReadAt: f.now, ObservedAt: f.now.Add(-time.Second)}}, nil
}

func (f *resourceFixture) ResourceEndpointStatus(_ context.Context, org, project string) (map[string]store.LayerStatus, error) {
	if org != "default" || project != "p" {
		return nil, errors.New("wrong scope")
	}
	return map[string]store.LayerStatus{"re_one": {LastReadAt: &f.now, LastGoodReadAt: &f.now, ErrorClass: store.ErrorUnreachable}}, nil
}

func TestResourceAPIScopeAndUnknowns(t *testing.T) {
	a, key := liveAPI(nil)
	f := &resourceFixture{now: time.Now().UTC()}
	a.ResourceEndpoints = f
	a.ResourceSamples = f
	a.Now = time.Now
	for _, tc := range []struct {
		project, key string
		status       int
	}{{"p", "", 401}, {"other", key, 403}, {"p", key, 200}} {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/org/default/project/"+tc.project+"/resources", nil)
		if tc.key != "" {
			req.Header.Set("Authorization", "Bearer "+tc.key)
		}
		w := httptest.NewRecorder()
		a.Routes().ServeHTTP(w, req)
		if w.Code != tc.status {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}
		if tc.status == 200 {
			if w.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("resource snapshot may be cached")
			}
			var got resourcesRead
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if len(got.Resources) != 1 || len(got.Resources[0].Samples) != 1 {
				t.Fatal("missing resource")
			}
			sample := got.Resources[0].Samples[0]
			if sample.CPUUsed != nil || sample.CPUCores != nil || sample.MemoryBytes != nil || sample.MemoryLimitBytes != nil {
				t.Fatal("missing measurements became zero")
			}
			if got.Resources[0].SourceStatus.ErrorClass == nil {
				t.Fatal("failed source looks healthy")
			}
		}
	}
	if f.reads != 1 {
		t.Fatal("unauthorized read reached persistence")
	}
	f.fail = true
	req := httptest.NewRequest(http.MethodGet, "/api/v1/org/default/project/p/resources", nil)
	req.Header.Set("Authorization", "Bearer "+key)
	w := httptest.NewRecorder()
	a.Routes().ServeHTTP(w, req)
	if w.Code != 500 || strings.Contains(w.Body.String(), "private database") {
		t.Fatal("storage failure leaked or was accepted")
	}
}
