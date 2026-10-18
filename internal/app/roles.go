package app

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/alexedwards/scs/pgxstore"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/klauspost/compress/gzhttp"
	"github.com/redis/go-redis/v9"
	"golang.org/x/sync/errgroup"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/console"
	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/ingest"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/obs"
	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/sink"
	"github.com/mahimailabs/voicegateway/internal/store/chstore"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// shutdownGrace is how long in-flight requests get to finish once the role is told to stop.
const shutdownGrace = 10 * time.Second

// Web serves HTTP until ctx is cancelled: /healthz, LiveKit webhooks, agent uploads and the
// dashboard API (getCall so far); MCP mounts here in a later phase. Connections open lazily, so
// web starts while a dependency is still coming up and answers 503 for what needs it.
func Web(ctx context.Context, c config.Config, log *slog.Logger) error {
	sealer, err := seal.New(c.SecretKey)
	if err != nil {
		return err
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	opts, err := redis.ParseURL(c.RedisURL)
	if err != nil {
		return fmt.Errorf("parse VOICEGATEWAY_REDIS_URL: %w", redactErr(err, c.RedisURL))
	}
	rdb := redis.NewClient(opts)
	defer func() { _ = rdb.Close() }()

	// The API reads what the worker stored; web never writes ClickHouse (CLAUDE.md Boundaries).
	events, err := chstore.OpenReadOnly(c.ClickHouseURL)
	if err != nil {
		return redactErr(err, c.ClickHouseURL)
	}
	defer func() { _ = events.Close() }()

	projects := pgstore.NewStore(pool, sealer)
	producer := stream.NewProducer(rdb, stream.Ingest)
	uploads := &ingest.Uploads{Projects: projects, Stream: producer, Log: log, Now: time.Now}

	warnRetiredToken(os.Getenv, log)
	trusted, err := auth.ParseTrusted(c.TrustedProxies)
	if err != nil {
		return fmt.Errorf("VOICEGATEWAY_TRUSTED_PROXIES: %w", err)
	}
	secure, plain := auth.NewSessions(pgxstore.New(pool))
	accounts := &auth.Service{
		Users: projects, Secure: secure, Plain: plain, Trusted: trusted,
		PublicHTTPS: strings.HasPrefix(strings.ToLower(c.PublicURL), "https://"),
		Log:         log, Now: time.Now,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", obs.Health)
	// LiveKit's webhooks and Agents' observability paths (decision 0014), under
	// LIVEKIT_OBSERVABILITY_URL. They take only POST; any other method is a 405, never the console.
	for path, h := range map[string]http.Handler{
		"/livekit/webhook":               &ingest.Webhooks{Projects: projects, Stream: producer, Log: log, Now: time.Now},
		"/observability/traces/otlp/v0":  uploads.OTLP(event.KindAgentTraces),
		"/observability/logs/otlp/v0":    uploads.OTLP(event.KindAgentLogs),
		"/observability/metrics/otlp/v0": uploads.OTLP(event.KindAgentMetrics),
		"/observability/recordings/v0":   http.HandlerFunc(uploads.SessionReport),
	} {
		mux.Handle("POST "+path, h)
		mux.HandleFunc(path, postOnly)
	}
	mux.Handle("/auth/", accounts.Routes())
	mux.Handle("/api/v1/", (&api.API{
		Projects: projects, Calls: projects, Traces: events, Layers: events, Endpoints: projects, Accounts: projects, Stats: projects, Keys: projects, NewKey: auth.NewKey, Log: log, Now: time.Now, Gate: accounts.Gate,
		AnswerBudgetMs: c.AnswerBudgetMs,
	}).Routes())
	if c.ConsoleDir != "" {
		dist := os.DirFS(c.ConsoleDir)
		if _, err := fs.Stat(dist, "index.html"); err != nil {
			return fmt.Errorf("VOICEGATEWAY_CONSOLE_DIR %q holds no built console: %w", c.ConsoleDir, err)
		}
		// The least specific pattern: every route above wins over it.
		// gzhttp compresses the console's files: the largest chunk is about 4 MB as built.
		mux.Handle("/", gzhttp.GzipHandler(console.Handler(dist)))
	}

	srv := &http.Server{
		Addr: c.WebAddr,
		// Writes from another origin are refused on every route (decision 0027). LiveKit's webhooks
		// and agent uploads are server to server, carry no browser origin, and pass.
		Handler:           http.NewCrossOriginProtection().Handler(mux),
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

// Worker joins and stores what web appends to the stream, sweeps idle calls and unlinked traces
// once a minute, rebuilds stale call summaries every 10 s, and samples each project's LiveKit
// server until ctx is cancelled. It never answers HTTP (CLAUDE.md Boundaries).
func Worker(ctx context.Context, c config.Config, log *slog.Logger) error {
	sealer, err := seal.New(c.SecretKey)
	if err != nil {
		return fmt.Errorf("secret key: %w", err)
	}
	pool, err := pgxpool.New(ctx, c.PostgresURL)
	if err != nil {
		return fmt.Errorf("postgres: %w", redactErr(err, c.PostgresURL))
	}
	defer pool.Close()
	opts, err := redis.ParseURL(c.RedisURL)
	if err != nil {
		return fmt.Errorf("parse VOICEGATEWAY_REDIS_URL: %w", redactErr(err, c.RedisURL))
	}
	rdb := redis.NewClient(opts)
	defer func() { _ = rdb.Close() }()
	events, err := chstore.Open(c.ClickHouseURL)
	if err != nil {
		return redactErr(err, c.ClickHouseURL)
	}
	defer func() { _ = events.Close() }()
	// The call index needs no sealer: it never reads a LiveKit secret.
	joiner := &join.Joiner{Calls: pgstore.NewStore(pool, nil), Traces: events, Log: log, Timeout: 30 * time.Second}
	// One consumer per worker process; a worker that dies leaves its entries to be claimed.
	name, err := os.Hostname()
	if err != nil {
		return fmt.Errorf("consumer name: %w", err)
	}

	log.Info("worker started", "consumer", name)
	g, gctx := errgroup.WithContext(ctx)
	g.Go(func() error {
		return (&scrape.Scraper{
			Projects:  pgstore.NewStore(pool, sealer),
			Samples:   events,
			Lock:      &scrape.RedisLock{Client: rdb},
			Dial:      scrape.DialSDK,
			ID:        name,
			Log:       log,
			Now:       time.Now,
			Every:     10 * time.Second,
			FullEvery: time.Minute,
			Timeout:   10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		return (&scrape.SIPScraper{
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     scrape.DialSDK,
			Log:      log,
			Now:      time.Now,
			Every:    time.Minute,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		return (&scrape.EgressScraper{
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     scrape.DialSDK,
			Log:      log,
			Now:      time.Now,
			Every:    15 * time.Second,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		return (&scrape.IngressScraper{
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     scrape.DialSDK,
			Log:      log,
			Now:      time.Now,
			Every:    15 * time.Second,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		return (&scrape.CloudAgentScraper{
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     scrape.DialSDK,
			Log:      log,
			Now:      time.Now,
			Every:    time.Minute,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		endpoints := pgstore.NewStore(pool, sealer)
		return (&scrape.WorkerScraper{
			Projects:  endpoints,
			Endpoints: endpoints,
			Samples:   events,
			Lock:      &scrape.RedisLock{Client: rdb},
			Client:    scrape.NewWorkerClient(),
			Log:       log,
			Now:       time.Now,
			Every:     15 * time.Second,
			Timeout:   10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		every(gctx, sweepEvery, "sweep", joiner.Sweep, log)
		return nil
	})
	g.Go(func() error {
		every(gctx, summarizeEvery, "summarize", joiner.Summarize, log)
		return nil
	})
	g.Go(func() error {
		return (&sink.Sink{
			Stream:    stream.NewConsumer(rdb, stream.Ingest, workerGroup, name),
			Events:    events,
			Join:      joiner,
			Log:       log,
			Batch:     1000,
			Block:     time.Second,
			ClaimIdle: time.Minute,
			Backoff:   250 * time.Millisecond, MaxBackoff: 30 * time.Second,
			Timeout: 30 * time.Second,
		}).Run(gctx)
	})
	err = g.Wait()
	log.Info("worker stopped")
	return err
}

// sweepEvery is how often the worker closes idle calls and counts unlinked traces;
// summarizeEvery is how often it rebuilds stale call summaries (phase 04d). Every worker runs both;
// both are safe to run twice.
const (
	sweepEvery     = time.Minute
	summarizeEvery = 10 * time.Second
)

// every runs f each period until ctx is cancelled. A failure is logged and retried next period.
func every(ctx context.Context, period time.Duration, what string, f func(context.Context, time.Time) error, log *slog.Logger) {
	t := time.NewTicker(period)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-t.C:
			cctx, cancel := context.WithTimeout(ctx, period)
			if err := f(cctx, now); err != nil && ctx.Err() == nil {
				log.Error("worker: "+what+" failed, retrying next period", "err", err)
			}
			cancel()
		}
	}
}

// workerGroup is the consumer group every worker reads the ingest stream in.
const workerGroup = "worker"

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

// warnRetiredToken logs once when VOICEGATEWAY_API_TOKEN is still set. Phase 06c retired it for
// project API keys: it is no longer read or accepted, and web starts anyway, so an upgrade with an
// old .env keeps running.
func warnRetiredToken(getenv func(string) string, log *slog.Logger) {
	if getenv("VOICEGATEWAY_API_TOKEN") != "" {
		log.Warn("VOICEGATEWAY_API_TOKEN is no longer used and is ignored; create a project key with voicegateway key create, or in Settings, Access, API keys, then remove the variable")
	}
}

// postOnly answers a POST-only path asked with another method, as ServeMux would without the
// console's catch-all.
func postOnly(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Allow", http.MethodPost)
	http.Error(w, http.StatusText(http.StatusMethodNotAllowed), http.StatusMethodNotAllowed)
}
