// Command loadtest replays one captured LiveKit Agents call as many concurrent calls against a
// running VoiceGateway, then checks that every event it delivered was stored (docs/specs/load-test.md).
//
// Webhooks go through livekit/protocol's own URLNotifier, the sender livekit-server uses, with its
// queue and retries. Agent uploads are sent as LiveKit Agents sends them, and retried on 5xx the way
// its exporters do.
//
//	go run ./tools/loadtest -calls 1000 -ramp 20s
package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"slices"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/hashicorp/go-retryablehttp"
	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/logger"
	"github.com/livekit/protocol/webhook"
	"golang.org/x/sync/errgroup"
)

type config struct {
	baseURL, org, project string
	key, secret           string
	calls                 int
	ramp                  time.Duration
	stretch               float64
	sdk                   bool
	root, out             string
	drain                 time.Duration
	p99                   time.Duration
}

func main() {
	var c config
	flag.StringVar(&c.baseURL, "url", "http://127.0.0.1:8080", "VoiceGateway web base URL")
	flag.StringVar(&c.org, "org", "default", "organisation id")
	flag.StringVar(&c.project, "project", "load", "project id; use a project kept for load tests")
	flag.IntVar(&c.calls, "calls", 1000, "concurrent calls to replay")
	flag.DurationVar(&c.ramp, "ramp", 20*time.Second, "time over which the calls start, evenly spaced")
	flag.Float64Var(&c.stretch, "stretch", 1, "multiply every gap in the captured call, to hold the calls open longer")
	flag.BoolVar(&c.sdk, "sdk", true, "send the VoiceGateway SDK batch of each call too")
	flag.StringVar(&c.root, "root", ".", "repository root, where the captured call is read from")
	flag.StringVar(&c.out, "out", "", "write the report as JSON to this file")
	flag.DurationVar(&c.p99, "p99", 50*time.Millisecond, "slowest p99 answer allowed for each upload kind (ARCHITECTURE.md's ingest target)")
	flag.DurationVar(&c.drain, "drain", 10*time.Minute, "how long to wait for the worker to store everything, and again for every call summary")
	flag.Parse()
	c.key, c.secret = os.Getenv("LIVEKIT_API_KEY"), os.Getenv("LIVEKIT_API_SECRET")

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	log := slog.New(slog.NewTextHandler(os.Stderr, nil))
	if err := run(ctx, c, log); err != nil {
		log.Error("load test failed", "err", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, c config, log *slog.Logger) error {
	if c.key == "" || c.secret == "" {
		return errors.New("set LIVEKIT_API_KEY and LIVEKIT_API_SECRET to the project's LiveKit key and secret")
	}
	if c.calls < 1 || c.stretch <= 0 {
		return errors.New("-calls must be at least 1 and -stretch above 0")
	}
	stores, err := openStores(ctx)
	if err != nil {
		return err
	}
	defer stores.close()

	f, err := loadFixture(c.root)
	if err != nil {
		return err
	}
	run := runTag()
	begin := time.Now().UTC().Truncate(time.Second)
	firstStart, plans, err := planAll(ctx, f, c, run)
	if err != nil {
		return err
	}
	deadLetters, err := stores.deadLetters(ctx)
	if err != nil {
		return err
	}
	log.Info("replaying", "run", run, "calls", c.calls, "ramp", c.ramp, "stretch", c.stretch, "steps_per_call", len(plans[0]))

	rec := newRecorder()
	base := fmt.Sprintf("%s/ingest/%s/%s", c.baseURL, c.org, c.project)
	// livekit-server registers the notifier's counters at start; the notifier panics without them.
	webhook.InitWebhookStats(nil)
	notifier := webhook.NewURLNotifier(webhook.URLNotifierParams{
		Logger: logger.GetDiscardLogger(), URL: base + "/livekit/webhook", APIKey: c.key, APISecret: c.secret,
	})
	notifier.RegisterProcessedHook(func(_ context.Context, whi *livekit.WebhookInfo) { rec.webhook(whi) })
	up := newUploader(c, base, rec)

	sampleCtx, stopSampling := context.WithCancel(ctx)
	sampled := make(chan []sample, 1)
	go func() { sampled <- stores.sample(sampleCtx, rec) }()

	var wg sync.WaitGroup
	for n := range c.calls {
		wg.Go(func() { replay(ctx, plans[n], notifier, up, rec) })
	}
	wg.Wait()
	notifier.Stop(false)
	sent := time.Now()
	log.Info("all calls sent; waiting for the worker", "took", sent.Sub(firstStart).Round(time.Millisecond))

	drained, err := stores.waitDrained(ctx, c.drain)
	if err != nil {
		stopSampling()
		return err
	}
	summarised, err := stores.waitSummaries(ctx, c, run, c.drain)
	stopSampling()
	samples := <-sampled
	if err != nil {
		return err
	}
	rep := rec.report(c, run, firstStart, sent, drained, samples)
	rep.SummariesTook = summarised.Sub(sent).Round(time.Millisecond).String()
	if rep.Stored, err = stores.verify(ctx, c, run, begin, deadLetters); err != nil {
		return err
	}
	rep.judge(c.calls, c.p99)
	rep.print(os.Stdout)
	if c.out != "" {
		b, err := json.MarshalIndent(rep, "", "  ")
		if err != nil {
			return err
		}
		if err := os.WriteFile(c.out, b, 0o600); err != nil {
			return err
		}
	}
	if !rep.Pass {
		return errors.New("events were lost or refused; see the report")
	}
	return nil
}

// planAll builds every call's uploads before the first call starts, on every core. Plans carry
// absolute send times, so the head start is measured from one plan first: a plan that is still
// being built when its call should start would send a burst no LiveKit server sends.
func planAll(ctx context.Context, f *fixture, c config, run string) (time.Time, [][]step, error) {
	t := time.Now()
	if _, err := f.plan(run, 0, t, c.stretch, c.sdk); err != nil {
		return time.Time{}, nil, fmt.Errorf("plan: %w", err)
	}
	workers := runtime.GOMAXPROCS(0)
	estimate := time.Since(t) * time.Duration(c.calls/workers+1)
	firstStart := time.Now().Add(2*estimate + 2*time.Second).Truncate(time.Millisecond)
	gap := c.ramp / time.Duration(c.calls)
	plans := make([][]step, c.calls)
	g, _ := errgroup.WithContext(ctx)
	g.SetLimit(workers)
	for n := range c.calls {
		g.Go(func() error {
			var err error
			if plans[n], err = f.plan(run, n, firstStart.Add(time.Duration(n)*gap), c.stretch, c.sdk); err != nil {
				return fmt.Errorf("plan call %d: %w", n, err)
			}
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return time.Time{}, nil, err
	}
	if late := time.Since(firstStart); late > 0 {
		return time.Time{}, nil, fmt.Errorf("planning ran %s past the first call's start; the replay would burst", late.Round(time.Millisecond))
	}
	return firstStart, plans, nil
}

// runTag is four base62 characters that mark every id of one run.
func runTag() string {
	const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
	b := make([]byte, 4)
	_, _ = rand.Read(b)
	for i := range b {
		b[i] = alphabet[int(b[i])%len(alphabet)]
	}
	return string(b)
}

// replay sends one call's steps, each at its moment.
func replay(ctx context.Context, steps []step, notifier *webhook.URLNotifier, up *uploader, rec *recorder) {
	// A call is live from its first step, not while it waits for its turn in the ramp.
	select {
	case <-ctx.Done():
		return
	case <-time.After(time.Until(steps[0].at)):
	}
	rec.live.Add(1)
	defer rec.live.Add(-1)
	for _, s := range steps {
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Until(s.at)):
		}
		if s.kind == kindWebhook {
			rec.queued(kindWebhook)
			if err := notifier.QueueNotify(ctx, s.webhook); err != nil {
				rec.failed(kindWebhook, err)
			}
			continue
		}
		up.send(ctx, s)
	}
}

// uploader sends agent and SDK uploads with a bearer token holding the observability write grant,
// as LiveKit Agents does, and retries a 5xx or a refused connection.
type uploader struct {
	client *retryablehttp.Client
	base   string
	token  func() (string, error)
	rec    *recorder
}

func newUploader(c config, base string, rec *recorder) *uploader {
	client := retryablehttp.NewClient()
	client.Logger = nil
	client.RetryMax = 8
	client.RetryWaitMin = 500 * time.Millisecond
	client.RetryWaitMax = 5 * time.Second
	client.HTTPClient.Timeout = 30 * time.Second
	// Time every attempt, so a retried request still counts each answer it waited for.
	client.HTTPClient.Transport = timed{next: client.HTTPClient.Transport, rec: rec}
	var mu sync.Mutex
	var tok string
	var until time.Time
	return &uploader{client: client, base: base, rec: rec, token: func() (string, error) {
		mu.Lock()
		defer mu.Unlock()
		if time.Now().Before(until) {
			return tok, nil
		}
		at := auth.NewAccessToken(c.key, c.secret).SetValidFor(10 * time.Minute).
			SetObservabilityGrant(&auth.ObservabilityGrant{Write: true})
		t, err := at.ToJWT()
		if err != nil {
			return "", err
		}
		tok, until = t, time.Now().Add(5*time.Minute)
		return tok, nil
	}}
}

var paths = map[kind]string{
	kindTraces:  "/observability/traces/otlp/v0",
	kindMetrics: "/observability/metrics/otlp/v0",
	kindReport:  "/observability/recordings/v0",
	kindSDK:     "/sdk/v1/events",
}

type kindKey struct{}

func (u *uploader) send(ctx context.Context, s step) {
	u.rec.queued(s.kind)
	tok, err := u.token()
	if err != nil {
		u.rec.failed(s.kind, err)
		return
	}
	req, err := retryablehttp.NewRequestWithContext(context.WithValue(ctx, kindKey{}, s.kind), http.MethodPost, u.base+paths[s.kind], bytes.NewReader(s.body))
	if err != nil {
		u.rec.failed(s.kind, err)
		return
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Content-Type", s.ctype)
	res, err := u.client.Do(req)
	if err != nil {
		u.rec.failed(s.kind, err)
		return
	}
	_, _ = io.Copy(io.Discard, res.Body)
	_ = res.Body.Close()
	if res.StatusCode/100 != 2 {
		u.rec.failed(s.kind, fmt.Errorf("answered %d", res.StatusCode))
		return
	}
	u.rec.accepted(s.kind, s.spans)
}

type timed struct {
	next http.RoundTripper
	rec  *recorder
}

func (t timed) RoundTrip(r *http.Request) (*http.Response, error) {
	start := time.Now()
	res, err := t.next.RoundTrip(r)
	k, _ := r.Context().Value(kindKey{}).(kind)
	status := 0
	if res != nil {
		status = res.StatusCode
	}
	t.rec.attempt(k, time.Since(start), status, err)
	return res, err
}

// recorder counts what was sent and how it was answered.
type recorder struct {
	mu        sync.Mutex
	latencies map[kind][]time.Duration
	// webhookQueue is how long each webhook waited in the notifier's queue before its send began.
	webhookQueue []time.Duration
	dropTimes    []time.Time
	counts       map[kind]*counts
	errs         map[string]int
	live         atomic.Int64
}

type counts struct {
	Queued   int `json:"queued"`
	Accepted int `json:"accepted"`
	Failed   int `json:"failed"`
	Attempts int `json:"attempts"`
	Retried  int `json:"answers_not_2xx"`
	Dropped  int `json:"dropped_by_notifier"`
	Spans    int `json:"spans"`
}

func newRecorder() *recorder {
	return &recorder{latencies: map[kind][]time.Duration{}, counts: map[kind]*counts{}, errs: map[string]int{}}
}

func (r *recorder) c(k kind) *counts {
	if r.counts[k] == nil {
		r.counts[k] = &counts{}
	}
	return r.counts[k]
}

func (r *recorder) queued(k kind) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.c(k).Queued++
}

func (r *recorder) accepted(k kind, spans int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.c(k).Accepted++
	r.c(k).Spans += spans
}

func (r *recorder) failed(k kind, err error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.c(k).Failed++
	r.errs[string(k)+": "+err.Error()]++
}

func (r *recorder) attempt(k kind, d time.Duration, status int, err error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.c(k).Attempts++
	r.latencies[k] = append(r.latencies[k], d)
	if err != nil || status/100 != 2 {
		r.c(k).Retried++
	}
}

// webhook records one delivery by the URLNotifier. Its send time includes its own retries, and it
// reports no status, so a webhook refused with a 4xx is found by the store check instead.
func (r *recorder) webhook(whi *livekit.WebhookInfo) {
	r.mu.Lock()
	defer r.mu.Unlock()
	c := r.c(kindWebhook)
	switch {
	case whi.GetIsDropped():
		c.Dropped++
		c.Failed++
		r.dropTimes = append(r.dropTimes, time.Now())
	case whi.GetSendError() != "":
		c.Failed++
		r.errs["webhook: "+whi.GetSendError()]++
	default:
		c.Accepted++
		c.Attempts++
		r.webhookQueue = append(r.webhookQueue, time.Duration(whi.GetQueueDurationNs()))
		r.latencies[kindWebhook] = append(r.latencies[kindWebhook], time.Duration(whi.GetSendDurationNs()))
	}
}

// quantile is the nearest-rank quantile p of sorted durations.
func quantile(sorted []time.Duration, p float64) time.Duration {
	return sorted[min(len(sorted)-1, int(p*float64(len(sorted))))]
}

func quantiles(ds []time.Duration) map[string]string {
	if len(ds) == 0 {
		return nil
	}
	s := slices.Clone(ds)
	slices.Sort(s)
	q := func(p float64) string { return quantile(s, p).Round(10 * time.Microsecond).String() }
	return map[string]string{"p50": q(0.50), "p95": q(0.95), "p99": q(0.99), "max": s[len(s)-1].Round(10 * time.Microsecond).String()}
}
