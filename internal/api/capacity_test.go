package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestCapacityRuns: an Admin starts one capacity test per project and stops it; a Viewer reads it
// but cannot start one; a bad request never stores a run.
func TestCapacityRuns(t *testing.T) {
	f := newFixture(t)
	admin := f.api(nil)
	admin.Capacity = f.pg
	viewer := f.api(nil)
	viewer.Capacity = f.pg
	viewer.Gate = gateAs(map[string]string{"default": "viewer"})
	base := "/api/v1/org/default/project/" + f.project

	send := func(t *testing.T, a *API, method, path, body string) (int, []byte) {
		t.Helper()
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
		}
		rec := httptest.NewRecorder()
		a.Routes().ServeHTTP(rec, req)
		f.validateResponse(t, httptest.NewRequest(method, path, strings.NewReader(body)), rec.Code, rec.Result().Header, rec.Body.Bytes())
		return rec.Code, rec.Body.Bytes()
	}
	errCode := func(b []byte) string {
		var e struct {
			Code string `json:"code"`
		}
		_ = json.Unmarshal(b, &e)
		return e.Code
	}

	if code, b := send(t, admin, http.MethodGet, base+"/capacity?agent=vg-capture", ""); code != http.StatusOK || strings.TrimSpace(string(b)) != "null" {
		t.Errorf("before the first run = %d %s, want 200 null", code, b)
	}
	if code, _ := send(t, admin, http.MethodGet, base+"/capacity", ""); code != http.StatusBadRequest {
		t.Errorf("no agent = %d, want 400", code)
	}

	for name, body := range map[string]string{
		"no agent":      `{"maxStep":16,"consent":true}`,
		"control char":  `{"agent":"a\u0007","maxStep":16,"consent":true}`,
		"odd max step":  `{"agent":"vg-capture","maxStep":12,"consent":true}`,
		"no consent":    `{"agent":"vg-capture","maxStep":16,"consent":false}`,
		"unknown field": `{"agent":"vg-capture","maxStep":16,"consent":true,"steps":[1]}`,
	} {
		if code, b := send(t, admin, http.MethodPost, base+"/capacity-runs", body); code != http.StatusBadRequest || errCode(b) != codeInvalidRequest {
			t.Errorf("%s = %d %s, want 400 invalid_request", name, code, errCode(b))
		}
	}
	start := `{"agent":"vg-capture","maxStep":16,"consent":true}`
	if code, _ := send(t, viewer, http.MethodPost, base+"/capacity-runs", start); code != http.StatusForbidden {
		t.Errorf("viewer start = %d, want 403", code)
	}

	code, b := send(t, admin, http.MethodPost, base+"/capacity-runs", start)
	var run capacityRun
	if err := json.Unmarshal(b, &run); err != nil || code != http.StatusAccepted || run.State != "requested" || run.Capacity != nil {
		t.Fatalf("start = %d %s, want 202 with a requested run and no capacity", code, b)
	}
	if code, b := send(t, admin, http.MethodPost, base+"/capacity-runs", start); code != http.StatusConflict || errCode(b) != codeRunActive {
		t.Errorf("second start = %d %s, want 409 run_active", code, errCode(b))
	}
	if code, b := send(t, viewer, http.MethodGet, base+"/capacity?agent=vg-capture", ""); code != http.StatusOK || !strings.Contains(string(b), run.ID) {
		t.Errorf("viewer read = %d %s, want 200 with the run", code, b)
	}

	if code, _ := send(t, viewer, http.MethodPost, base+"/capacity-runs/"+run.ID+"/stop", ""); code != http.StatusForbidden {
		t.Errorf("viewer stop = %d, want 403", code)
	}
	code, b = send(t, admin, http.MethodPost, base+"/capacity-runs/"+run.ID+"/stop", "")
	var stopped capacityRun
	if err := json.Unmarshal(b, &stopped); err != nil || code != http.StatusOK || stopped.State != "ended" || stopped.LimitedBy == nil || *stopped.LimitedBy != "stopped" {
		t.Errorf("stop = %d %s, want 200 ended as stopped", code, b)
	}
	if code, _ := send(t, admin, http.MethodPost, base+"/capacity-runs/"+run.ID+"/stop", ""); code != http.StatusNotFound {
		t.Errorf("stop an ended run = %d, want 404", code)
	}
	if code, _ := send(t, admin, http.MethodPost, base+"/capacity-runs/not-a-uuid/stop", ""); code != http.StatusNotFound {
		t.Errorf("stop an unknown run = %d, want 404", code)
	}

	// An instance that reaches only public addresses refuses new tests but still answers reads.
	off := f.api(nil)
	off.Capacity, off.CapacityOff = f.pg, true
	if code, b := send(t, off, http.MethodPost, base+"/capacity-runs", start); code != http.StatusServiceUnavailable || errCode(b) != codeSourceUnavailable {
		t.Errorf("start with tests off = %d %s, want 503 source_unavailable", code, errCode(b))
	}
	if code, b := send(t, off, http.MethodGet, base+"/capacity?agent=vg-capture", ""); code != http.StatusOK || !strings.Contains(string(b), run.ID) {
		t.Errorf("read with tests off = %d %s, want 200 with the last run", code, b)
	}
}
