package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/obs"
)

// shutdownGrace is how long in-flight requests get to finish once the role is told to stop.
const shutdownGrace = 10 * time.Second

// Web serves HTTP until ctx is cancelled. Ingest, the dashboard API and MCP mount here in later
// phases; today it answers /healthz.
func Web(ctx context.Context, c config.Config, log *slog.Logger) error {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", obs.Health)

	srv := &http.Server{
		Addr:              c.WebAddr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		BaseContext:       func(net.Listener) context.Context { return ctx },
	}
	ln, err := net.Listen("tcp", c.WebAddr)
	if err != nil {
		return fmt.Errorf("listen on %s: %w", c.WebAddr, err)
	}
	log.Info("web started", "addr", ln.Addr().String())

	served := make(chan error, 1)
	go func() { served <- srv.Serve(ln) }()

	select {
	case err := <-served:
		return fmt.Errorf("serve: %w", err)
	case <-ctx.Done():
	}
	log.Info("web stopping")
	sctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), shutdownGrace)
	defer cancel()
	if err := srv.Shutdown(sctx); err != nil {
		return fmt.Errorf("shutdown: %w", err)
	}
	if err := <-served; err != nil && !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("serve: %w", err)
	}
	log.Info("web stopped")
	return nil
}

// Worker runs until ctx is cancelled. Stream consumers, settling and scraping join it in later
// phases; it never answers HTTP (CLAUDE.md Boundaries).
func Worker(ctx context.Context, _ config.Config, log *slog.Logger) error {
	log.Info("worker started")
	<-ctx.Done()
	log.Info("worker stopped")
	return nil
}

// containsSecret reports whether text repeats the URL or its password.
func containsSecret(text, raw string) bool {
	if raw != "" && strings.Contains(text, raw) {
		return true
	}
	u, err := url.Parse(raw)
	if err != nil {
		return true
	}
	if p, ok := u.User.Password(); ok && p != "" && strings.Contains(text, p) {
		return true
	}
	return false
}
