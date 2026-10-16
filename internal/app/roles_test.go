package app

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"net"
	"net/http"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/seal"
)

func TestWebServesHealthAndStopsCleanly(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	// Nothing listens on these; web must still start and answer /healthz.
	c := config.Config{
		WebAddr:     addr,
		PostgresURL: "postgres://voicegateway@127.0.0.1:1/voicegateway",
		RedisURL:    "redis://127.0.0.1:1/0",
		SecretKey:   bytes.Repeat([]byte{7}, seal.KeySize),
	}
	go func() { done <- Web(ctx, c, slog.New(slog.NewTextHandler(io.Discard, nil))) }()

	var resp *http.Response
	for range 50 {
		resp, err = http.Get("http://" + addr + "/healthz")
		if err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err != nil {
		t.Fatalf("GET /healthz: %v", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("/healthz status = %d, want 200", resp.StatusCode)
	}

	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Errorf("Web() = %v after cancel, want nil", err)
		}
	case <-time.After(shutdownGrace):
		t.Fatal("Web() did not stop after cancel")
	}
}

func TestContainsSecret(t *testing.T) {
	raw := "redis://:redis-secret@redis:6379/0"
	tests := []struct {
		text string
		want bool
	}{
		{"dial tcp redis:6379: connection refused", false},
		{"bad URL redis://:redis-secret@redis:6379/0", true},
		{"auth failed for redis-secret", true},
	}
	for _, tt := range tests {
		if got := containsSecret(tt.text, raw); got != tt.want {
			t.Errorf("containsSecret(%q) = %v, want %v", tt.text, got, tt.want)
		}
	}
}
