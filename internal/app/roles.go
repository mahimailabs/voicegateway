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
	"github.com/go-logr/logr"
	"github.com/jackc/pgx/v5/pgxpool"
	lklogger "github.com/livekit/protocol/logger"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/redis/go-redis/v9"
	"golang.org/x/sync/errgroup"

	apicontract "github.com/mahimailabs/voicegateway/docs/api"
	"github.com/mahimailabs/voicegateway/ee/instance"
	"github.com/mahimailabs/voicegateway/internal/alert"
	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/bucket"
	"github.com/mahimailabs/voicegateway/internal/capacity"
	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/console"
	"github.com/mahimailabs/voicegateway/internal/ingest"
	"github.com/mahimailabs/voicegateway/internal/invite"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/mailer"
	"github.com/mahimailabs/voicegateway/internal/mcp"
	"github.com/mahimailabs/voicegateway/internal/oauth"
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
	live := stream.NewLive(opts, log)
	defer func() { _ = live.Close() }()

	// The API reads what the worker stored; web never writes ClickHouse (CLAUDE.md Boundaries).
	events, err := chstore.OpenReadOnly(c.ClickHouseURL)
	if err != nil {
		return redactErr(err, c.ClickHouseURL)
	}
	defer func() { _ = events.Close() }()

	projects := pgstore.NewStore(pool, sealer)
	producer := stream.NewProducer(rdb, stream.Ingest)
	var quota ingest.Quota
	if c.CloudQuotas {
		quota = &instance.Quota{Store: projects, Now: time.Now}
	}
	instanceBucket, err := bucket.New(c.S3)
	if err != nil {
		return err
	}
	// A bucket a project brought is dialed through the worker's address guard, as its LiveKit URL is.
	bucketTransport := scrape.NewWorkerClient(c.WorkerEndpointsPublicOnly).Transport
	recordings := &bucket.Buckets{Default: instanceBucket, Projects: projects, Transport: bucketTransport}

	warnRetiredToken(os.Getenv, log)
	trusted, err := auth.ParseTrusted(c.TrustedProxies)
	if err != nil {
		return fmt.Errorf("VOICEGATEWAY_TRUSTED_PROXIES: %w", err)
	}
	// Sessions are written only when they change, or when their expiry has moved 5 minutes; the
	// idle timeout would otherwise rewrite the row on every request.
	secure, plain := auth.NewSessions(auth.NewQuietStore(pgxstore.New(pool), 5*time.Minute))
	links := &invite.Service{Store: projects, Hash: auth.HashPassword, Verify: auth.VerifyPassword, Log: log, Now: time.Now}
	// Without SMTP the service returns each link for the admin to copy; with it, links go by email
	// only.
	if c.SMTPURL != "" {
		// An emailed link's base must not come from the request's Host header, which any admin's
		// client sets: it would send the token to a host of their choosing.
		if c.PublicURL == "" {
			return errors.New("VOICEGATEWAY_SMTP_URL needs VOICEGATEWAY_PUBLIC_URL, the base of every emailed link")
		}
		m, err := mailer.New(c.SMTPURL, c.SMTPFrom)
		if err != nil {
			return err
		}
		links.Mailer = m
	}
	embeds, err := auth.NewEmbedTokens(c.SecretKey)
	if err != nil {
		return err
	}
	accounts := &auth.Service{
		Users: projects, Secure: secure, Plain: plain, Trusted: trusted,
		PublicHTTPS: strings.HasPrefix(strings.ToLower(c.PublicURL), "https://"),
		PublicURL:   c.PublicURL, Links: links,
		InstanceTokenSHA256: c.InstanceTokenSHA256, Embeds: embeds,
		Providers: signInProviders(c), Identities: projects,
		Log: log, Now: time.Now,
	}
	// The instance API (phase 06e, ee/instance) is on only with VOICEGATEWAY_INSTANCE_TOKEN.
	var instanceAPI api.InstanceHandlers
	if c.InstanceTokenSHA256 != nil {
		// An invitation link's base never comes from the request's Host header.
		if c.PublicURL == "" {
			return errors.New("VOICEGATEWAY_INSTANCE_TOKEN needs VOICEGATEWAY_PUBLIC_URL, the base of every invitation link")
		}
		instanceAPI = NewInstanceAPI(projects, links, c.PublicURL, log)
		if c.CloudQuotas {
			instanceAPI = (&instance.Handlers{Store: projects, Entitlements: projects, Invites: links, BaseURL: c.PublicURL, Log: log}).API()
		}
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", obs.Health)
	connections := &ingest.Connections{Store: projects, Log: log, Now: time.Now}
	mountIngestion(mux, projects, producer, quota, recordings, connections, log, time.Now)
	mux.Handle("/auth/", accounts.Routes())
	v1 := &api.API{
		Alerts: projects, AlertEmail: c.SMTPURL != "", AlertHTTPSOnly: c.WorkerEndpointsPublicOnly, AlertTest: alert.Composer{PublicURL: c.PublicURL, Now: time.Now}.Test,
		Settings: projects, Origins: projects, Embeds: embeds, Metadata: projects, Issues: projects, StorageBucket: c.S3.Bucket,
		Live:              live,
		ResourceEndpoints: projects, ResourceSamples: events, Projects: projects, NewProjects: projects, Credentials: projects, VerifyLiveKit: scrape.NewVerifier(c.WorkerEndpointsPublicOnly), VerifyBucket: bucket.NewVerifier(bucketTransport), Calls: projects, Capacity: projects, CapacityOff: c.WorkerEndpointsPublicOnly, Traces: events, Layers: events, Endpoints: projects, Accounts: projects, Stats: projects, Keys: projects, NewKey: auth.NewKey, Log: log, Now: time.Now, Gate: accounts.Gate,
		Invitations: projects, Inviter: NewInviter(links), LinkBase: accounts.LinkBase,
		Recordings: projects, Media: recordings,
		Connections: projects, PublicURL: c.PublicURL,
		Instance: instanceAPI,
	}
	mux.Handle("/api/v1/", v1.Routes())
	// MCP (PRODUCT.md): its tools call the /api/v1 router in process, as the key the same gate
	// identified in front of /mcp, so that router runs without a gate of its own.
	tools := *v1
	tools.Gate = nil
	inProcess, ok := tools.Routes().(mcp.API)
	if !ok {
		return errors.New("the API router is not a chi router")
	}
	agents, err := mcp.New(apicontract.OpenAPI, inProcess, log)
	if err != nil {
		return err
	}
	mux.Handle("/mcp", agents.Handler(accounts.Gate))
	if c.ConsoleDir != "" {
		dist := os.DirFS(c.ConsoleDir)
		if _, err := fs.Stat(dist, "index.html"); err != nil {
			return fmt.Errorf("VOICEGATEWAY_CONSOLE_DIR %q holds no built console: %w", c.ConsoleDir, err)
		}
		mountConsole(mux, dist, accounts.Frames)
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
	live := stream.NewLive(opts, log)
	defer func() { _ = live.Close() }()
	events, err := chstore.Open(c.ClickHouseURL)
	if err != nil {
		return redactErr(err, c.ClickHouseURL)
	}
	defer func() { _ = events.Close() }()
	// The call index needs no sealer: it never reads a LiveKit secret.
	joiner := &join.Joiner{Changed: live.Notify, Calls: pgstore.NewStore(pool, nil), Traces: events, Log: log, Timeout: 30 * time.Second}
	instanceBucket, err := bucket.New(c.S3)
	if err != nil {
		return err
	}
	recordings := &bucket.Buckets{
		Default: instanceBucket, Projects: pgstore.NewStore(pool, sealer),
		Transport: scrape.NewWorkerClient(c.WorkerEndpointsPublicOnly).Transport,
	}
	retention := &bucket.Retention{Bucket: recordings, Links: pgstore.NewStore(pool, nil), Log: log}
	// One consumer per worker process; a worker that dies leaves its entries to be claimed.
	name, err := os.Hostname()
	if err != nil {
		return fmt.Errorf("consumer name: %w", err)
	}

	// With VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY, the LiveKit API reads dial through the same
	// address guard as the worker metrics endpoints.
	dial := scrape.NewDial(c.WorkerEndpointsPublicOnly)
	log.Info("worker started", "consumer", name)
	g, gctx := errgroup.WithContext(ctx)
	g.Go(func() error {
		return (&scrape.Scraper{
			Jobs:      pgstore.NewStore(pool, nil),
			Changed:   live.Notify,
			Projects:  pgstore.NewStore(pool, sealer),
			Samples:   events,
			Lock:      &scrape.RedisLock{Client: rdb},
			Dial:      dial,
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
			Findings: pgstore.NewStore(pool, nil),
			Changed:  live.Notify,
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     dial,
			Log:      log,
			Now:      time.Now,
			Every:    time.Minute,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		return (&scrape.EgressScraper{
			Findings: pgstore.NewStore(pool, nil),
			Changed:  live.Notify,
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     dial,
			Log:      log,
			Now:      time.Now,
			Every:    15 * time.Second,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		return (&scrape.IngressScraper{
			Findings: pgstore.NewStore(pool, nil),
			Changed:  live.Notify,
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     dial,
			Log:      log,
			Now:      time.Now,
			Every:    15 * time.Second,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		return (&scrape.CloudAgentScraper{
			Findings: pgstore.NewStore(pool, nil),
			Changed:  live.Notify,
			Projects: pgstore.NewStore(pool, sealer),
			Samples:  events,
			Lock:     &scrape.RedisLock{Client: rdb},
			Dial:     dial,
			Log:      log,
			Now:      time.Now,
			Every:    time.Minute,
			Timeout:  10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		endpoints := pgstore.NewStore(pool, sealer)
		return (&scrape.WorkerScraper{
			Changed:   live.Notify,
			Projects:  endpoints,
			Endpoints: endpoints,
			Samples:   events,
			Lock:      &scrape.RedisLock{Client: rdb},
			Client:    scrape.NewWorkerClient(c.WorkerEndpointsPublicOnly),
			Log:       log,
			Now:       time.Now,
			Every:     15 * time.Second,
			Timeout:   10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		endpoints := pgstore.NewStore(pool, sealer)
		return (&scrape.ResourceScraper{
			Changed: live.Notify, Projects: endpoints, Endpoints: endpoints, Samples: events,
			Lock: &scrape.RedisLock{Client: rdb}, Client: scrape.NewWorkerClient(c.WorkerEndpointsPublicOnly),
			Log: log, Now: time.Now, Every: 15 * time.Second, Timeout: 10 * time.Second,
		}).Run(gctx)
	})
	// The capacity test's callers join rooms through server-sdk-go, whose WebRTC stack logs every
	// connection step; only its warnings reach the worker's log.
	lksdk.SetLogger(lklogger.LogRLogger(logr.FromSlogHandler(warnOnly{log.Handler()})))
	g.Go(func() error {
		if c.WorkerEndpointsPublicOnly {
			// The callers' signalling and WebRTC dials do not go through the address guard, so an
			// instance that must reach only public addresses runs no capacity tests (web refuses them).
			log.Info("capacity: tests are off, VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY is set")
			return nil
		}
		runs := pgstore.NewStore(pool, sealer)
		return (&capacity.Runner{
			Runs: runs, Projects: runs, Dial: capacity.NewDial(scrape.NewWorkerClient(c.WorkerEndpointsPublicOnly)),
			CPU: capacity.HostCPU, ID: name, Log: log, Now: time.Now,
			Poll: 2 * time.Second, Beat: time.Second, Lost: 30 * time.Second, Unclaimed: 15 * time.Minute,
			StepFor: time.Minute, BusyEvery: 10 * time.Second, Timeout: 10 * time.Second,
		}).Run(gctx)
	})
	g.Go(func() error {
		every(gctx, sweepEvery, "sweep", joiner.Sweep, log)
		return nil
	})
	g.Go(func() error {
		evaluator := &scrape.JobEvaluator{Store: pgstore.NewStore(pool, nil), Changed: live.Notify}
		every(gctx, summarizeEvery, "job findings", evaluator.Tick, log)
		return nil
	})
	g.Go(func() error {
		every(gctx, summarizeEvery, "findings backfill", joiner.BackfillFindings, log)
		return nil
	})
	g.Go(func() error {
		every(gctx, summarizeEvery, "recording backfill", joiner.BackfillMediaFindings, log)
		return nil
	})
	g.Go(func() error {
		every(gctx, summarizeEvery, "summarize", joiner.Summarize, log)
		return nil
	})
	g.Go(func() error {
		every(gctx, expireRecordingsEvery, "recording retention", retention.Run, log)
		return nil
	})
	// Alerts (docs/specs/alerts-delivery.md): one worker evaluates at a time; every worker sends due
	// deliveries, each leased to one of them. Without SMTP, email deliveries fail visibly.
	alerts := pgstore.NewStore(pool, sealer)
	var mail alert.Mailer
	if c.SMTPURL != "" {
		m, err := mailer.New(c.SMTPURL, c.SMTPFrom)
		if err != nil {
			return err
		}
		mail = m
	}
	composer := alert.Composer{PublicURL: c.PublicURL, Now: time.Now}
	g.Go(func() error {
		evaluator := &alert.Evaluator{Store: alerts, Ingress: events, Compose: composer.Compose, Log: log}
		every(gctx, alertEvery, "alerts", evaluator.Tick, log)
		return nil
	})
	g.Go(func() error {
		deliverer := &alert.Deliverer{Store: alerts, HTTP: scrape.NewWorkerClient(c.WorkerEndpointsPublicOnly), Refused: scrape.Refused, HTTPSOnly: c.WorkerEndpointsPublicOnly, Mailer: mail, Log: log, Now: time.Now}
		everyWithin(gctx, deliverEvery, alert.TickBudget, "alert delivery", deliverer.Tick, log)
		return nil
	})
	g.Go(func() error {
		return (&sink.Sink{
			Changed:   live.Notify,
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
// summarizeEvery is how often it rebuilds stale call summaries (phase 04d); expireRecordingsEvery
// is how often it deletes recordings past retention (phase 07a); alertEvery is how often it
// evaluates alerts and deliverEvery how often it sends due alert deliveries. Every worker runs all
// of them; all are safe to run twice.
const (
	sweepEvery            = time.Minute
	summarizeEvery        = 10 * time.Second
	expireRecordingsEvery = time.Hour
	alertEvery            = 15 * time.Second
	deliverEvery          = 5 * time.Second
)

// every runs f each period until ctx is cancelled. A failure is logged and retried next period.
func every(ctx context.Context, period time.Duration, what string, f func(context.Context, time.Time) error, log *slog.Logger) {
	everyWithin(ctx, period, period, what, f, log)
}

// everyWithin is every with each run given up to budget; ticks that fall inside a longer run are
// dropped.
func everyWithin(ctx context.Context, period, budget time.Duration, what string, f func(context.Context, time.Time) error, log *slog.Logger) {
	t := time.NewTicker(period)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-t.C:
			cctx, cancel := context.WithTimeout(ctx, budget)
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

// mountConsole serves the built console on the least specific pattern, so every route registered
// on mux wins over it. The accept page holds a token in its fragment, so it sends no Referer and
// is never cached. frames decides who may frame each page (auth.Service.Frames): an embedded
// widget's allowed origin, otherwise the console alone.
func mountConsole(mux *http.ServeMux, dist fs.FS, frames func(http.Handler) http.Handler) {
	mux.Handle("/", frames(console.Handler(dist)))
	mux.Handle("GET /invite", frames(auth.LinkHeaders(console.Handler(dist))))
}

// handlePost registers each POST-only route, and a 405 for any other method on its path.
func handlePost(mux *http.ServeMux, routes map[string]http.Handler) {
	for path, h := range routes {
		mux.Handle("POST "+path, h)
		mux.HandleFunc(path, postOnly)
	}
}

// postOnly answers a POST-only path asked with another method, as ServeMux would without the
// console's catch-all.
func postOnly(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Allow", http.MethodPost)
	http.Error(w, http.StatusText(http.StatusMethodNotAllowed), http.StatusMethodNotAllowed)
}

// signInProviders are the Google, GitHub and Auth0 sign-ins configuration turns on (phase 06h,
// decision 0036); config has already refused a half-set app or a missing public URL.
func signInProviders(c config.Config) map[string]auth.Provider {
	on := map[string]auth.Provider{}
	if c.Google.On() {
		on["google"] = oauth.NewGoogle(oauth.Config{ClientID: c.Google.ClientID, ClientSecret: c.Google.ClientSecret, RedirectURL: config.Callback(c.PublicURL, "google")}, nil)
	}
	if c.GitHub.On() {
		on["github"] = oauth.NewGitHub(oauth.Config{ClientID: c.GitHub.ClientID, ClientSecret: c.GitHub.ClientSecret, RedirectURL: config.Callback(c.PublicURL, "github")}, nil)
	}
	if c.Auth0.On() {
		on["auth0"] = oauth.NewAuth0(c.Auth0.Domain, oauth.Config{ClientID: c.Auth0.ClientID, ClientSecret: c.Auth0.ClientSecret, RedirectURL: config.Callback(c.PublicURL, "auth0")}, nil)
	}
	return on
}

// warnOnly passes a handler's warnings and errors and drops the rest.
type warnOnly struct{ slog.Handler }

func (h warnOnly) Enabled(ctx context.Context, l slog.Level) bool {
	return l >= slog.LevelWarn && h.Handler.Enabled(ctx, l)
}

func (h warnOnly) WithAttrs(attrs []slog.Attr) slog.Handler {
	return warnOnly{h.Handler.WithAttrs(attrs)}
}

func (h warnOnly) WithGroup(name string) slog.Handler { return warnOnly{h.Handler.WithGroup(name)} }
