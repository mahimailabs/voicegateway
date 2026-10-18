package console

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
)

func TestHandler(t *testing.T) {
	dist := fstest.MapFS{
		"index.html":       {Data: []byte("<!doctype html><title>VoiceGateway</title>")},
		"favicon.svg":      {Data: []byte("<svg/>")},
		"assets/index.js":  {Data: []byte("console.log(1)")},
		"globe/world.json": {Data: []byte("{}")},
	}
	h := Handler(dist)

	tests := []struct {
		method     string
		name, path string
		status     int
		body       string
		cache      string
	}{
		{"", "root is the app", "/", http.StatusOK, "VoiceGateway", "no-cache"},
		{"", "deep link is the app", "/org/default/project/trial/calls", http.StatusOK, "VoiceGateway", "no-cache"},
		{"", "a directory is the app, not a listing", "/assets/", http.StatusOK, "VoiceGateway", "no-cache"},
		{"", "hashed asset is cached for good", "/assets/index.js", http.StatusOK, "console.log", immutable},
		{"", "other file is served", "/favicon.svg", http.StatusOK, "<svg/>", ""},
		{"", "missing asset is a 404", "/assets/old-abc123.js", http.StatusNotFound, "", ""},
		{"", "unknown API path is a 404", "/api/v2/calls", http.StatusNotFound, "", ""},
		{http.MethodPost, "a write is never the app", "/calls", http.StatusNotFound, "", ""},
		{"", "path escape is refused", "/../../etc/passwd", http.StatusBadRequest, "", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			method := tt.method
			if method == "" {
				method = http.MethodGet
			}
			h.ServeHTTP(rec, httptest.NewRequest(method, tt.path, nil))
			if rec.Code != tt.status {
				t.Fatalf("status %d, want %d", rec.Code, tt.status)
			}
			if !strings.Contains(rec.Body.String(), tt.body) {
				t.Errorf("body %q does not contain %q", rec.Body.String(), tt.body)
			}
			if got := rec.Header().Get("Cache-Control"); tt.status == http.StatusOK && got != tt.cache {
				t.Errorf("Cache-Control %q, want %q", got, tt.cache)
			}
		})
	}
}

func TestHandlerCompresses(t *testing.T) {
	big := strings.Repeat("console.log(1);", 2000)
	h := Handler(fstest.MapFS{
		"index.html":      {Data: []byte("<!doctype html>")},
		"assets/index.js": {Data: []byte(big)},
	})

	for _, accept := range []string{"gzip", ""} {
		req := httptest.NewRequest(http.MethodGet, "/assets/index.js", nil)
		if accept != "" {
			req.Header.Set("Accept-Encoding", accept)
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		got := rec.Header().Get("Content-Encoding")
		if accept == "gzip" && (got != "gzip" || rec.Body.Len() >= len(big)) {
			t.Errorf("with Accept-Encoding gzip: Content-Encoding %q, %d bytes of %d", got, rec.Body.Len(), len(big))
		}
		if accept == "" && (got != "" || rec.Body.String() != big) {
			t.Errorf("without Accept-Encoding: Content-Encoding %q, want the plain file", got)
		}
	}
}
