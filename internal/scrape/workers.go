package scrape

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net"
	"net/http"
	"net/netip"
	"slices"
	"syscall"
	"time"

	"code.dny.dev/ssrf"
	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/common/expfmt"
	"golang.org/x/sync/errgroup"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// The metrics LiveKit Agents exports from each worker's Prometheus endpoint, both gauges labelled
// nodename (livekit-agents telemetry/metrics.py: CPU_LOAD_GAUGE and RUNNING_JOB_GAUGE).
const (
	metricWorkerLoad = "lk_agents_worker_load"
	metricActiveJobs = "lk_agents_active_job_count"
	labelNodename    = "nodename"
)

// maxMetricsBody bounds what one endpoint read takes in; a worker's metrics are a few kilobytes. A
// larger answer is a failed read, never a truncated one stored as good.
const maxMetricsBody = 4 << 20

// maxEndpointWorkers bounds the workers (nodename series) one endpoint may report. LiveKit Agents
// reports one per process; more is a failed read.
const maxEndpointWorkers = 256

// endpointReads is how many endpoints of one project are read at the same time.
const endpointReads = 8

// WorkerScraper reads the Prometheus metrics endpoints a project lists for its self-hosted agent
// workers on a timer and stores what each worker reported. An endpoint that does not answer is
// recorded as an unreachable read, never as a worker with no load or no jobs.
type WorkerScraper struct {
	// Changed runs only after the read is durably stored, including failed source reads.
	Changed   func(context.Context, string, string)
	Projects  Projects
	Endpoints WorkerEndpoints
	Samples   WorkerSamples
	Lock      Locker
	// Client reads the endpoints. It must not follow redirects, so a URL's credentials never go to
	// another host; NewWorkerClient makes one.
	Client *http.Client
	Log    *slog.Logger
	Now    func() time.Time
	// Every is how often the endpoints are read.
	Every time.Duration
	// Timeout bounds each endpoint read.
	Timeout time.Duration
}

// NewWorkerClient returns an HTTP client for worker metrics endpoints. It does not follow
// redirects, so a redirect answers as an error read, and it checks every address it dials, after
// DNS, so a name that resolves somewhere it must not go is refused (code.dny.dev/ssrf):
//   - Link-local addresses, cloud metadata services among them, are always refused.
//   - Private and loopback addresses are allowed, since a self-hosted install's workers usually sit
//     on its own network, unless publicOnly is set; then every special-purpose range is refused.
func NewWorkerClient(publicOnly bool) *http.Client {
	dialer := &net.Dialer{Timeout: 10 * time.Second, Control: dialGuard(publicOnly)}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DialContext = dialer.DialContext
	transport.Proxy = nil // a proxy would dial on the worker's behalf, past the guard
	return &http.Client{
		Transport:     transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

// dialGuard is the net.Dialer Control that decides, per resolved address, whether the worker may
// dial it: metadata addresses first, in both modes, then code.dny.dev/ssrf.
func dialGuard(publicOnly bool) func(network, address string, c syscall.RawConn) error {
	opts := []ssrf.Option{ssrf.WithAnyPort()}
	if !publicOnly {
		opts = append(opts, ssrf.WithAllowedV4Prefixes(privateV4...), ssrf.WithAllowedV6Prefixes(privateV6...))
	}
	guard := ssrf.New(opts...)
	return func(network, address string, c syscall.RawConn) error {
		if metadataAddress(address) {
			return errMetadataAddress
		}
		return guard.Safe(network, address, c)
	}
}

// The private and loopback ranges a self-hosted install's workers may sit on. Link-local
// (169.254.0.0/16, fe80::/10) is deliberately absent.
var (
	privateV4 = []netip.Prefix{
		netip.MustParsePrefix("10.0.0.0/8"),
		netip.MustParsePrefix("172.16.0.0/12"),
		netip.MustParsePrefix("192.168.0.0/16"),
		netip.MustParsePrefix("100.64.0.0/10"),
		netip.MustParsePrefix("127.0.0.0/8"),
	}
	privateV6 = []netip.Prefix{
		netip.MustParsePrefix("fc00::/7"),
		netip.MustParsePrefix("::1/128"),
	}
	// metadata holds cloud metadata addresses inside the ranges allowed above, refused in both
	// modes: Alibaba Cloud's (in 100.64.0.0/10), and AWS's and GCP's IPv6 ones (in fc00::/7). The
	// link-local ones (169.254.169.254 and the rest) the library refuses already.
	metadata = []netip.Prefix{
		netip.MustParsePrefix("100.100.100.200/32"),
		netip.MustParsePrefix("fd00:ec2::254/128"),
		netip.MustParsePrefix("fd20:ce::254/128"),
	}
)

var errMetadataAddress = errors.New("worker endpoint: a cloud metadata address is never dialled")

// metadataAddress reports whether a dial target is a cloud metadata address the private allow list
// would otherwise let through. A target that is not ip:port is left to the library, which refuses
// it.
func metadataAddress(address string) bool {
	ap, err := netip.ParseAddrPort(address)
	if err != nil {
		return false
	}
	ip := ap.Addr().Unmap()
	for _, p := range metadata {
		if p.Contains(ip) {
			return true
		}
	}
	return false
}

// Run reads every project once per Every until ctx is cancelled. It returns nil on cancellation.
func (s *WorkerScraper) Run(ctx context.Context) error {
	t := time.NewTicker(s.Every)
	defer t.Stop()
	for {
		s.Tick(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
	}
}

// Tick reads every project's worker endpoints once.
func (s *WorkerScraper) Tick(ctx context.Context) {
	projects, err := s.Projects.Projects(ctx)
	if err != nil {
		if ctx.Err() == nil {
			s.Log.Error("scrape: list projects", "err", err)
		}
		return
	}
	eachProject(projects, func(p store.Project) {
		if err := s.Workers(ctx, p); err != nil && ctx.Err() == nil {
			s.Log.Error("scrape: workers", "organisation", p.OrganisationID, "project", p.ID, "err", err)
		}
	})
}

// Workers reads one project's listed endpoints and stores what they reported with the read. A
// project that lists none is not read. It skips the project when another worker holds its lock for
// this interval. The layer read is good when at least one endpoint answered; each endpoint's own
// read is stored as well, so one that stops answering is named.
func (s *WorkerScraper) Workers(ctx context.Context, p store.Project) error {
	endpoints, err := s.Endpoints.OpenWorkerEndpoints(ctx, p.OrganisationID, p.ID)
	if err != nil {
		return fmt.Errorf("worker endpoints: %w", err)
	}
	if len(endpoints) == 0 {
		return nil
	}
	hold := s.Every * 9 / 10
	got, err := s.Lock.Lock(ctx, lockKey(p, store.LayerWorkers), hold)
	if err != nil {
		return fmt.Errorf("lock: %w", err)
	}
	if !got {
		return nil
	}

	rctx, cancel := context.WithTimeout(ctx, hold)
	results := make([][]workerMetrics, len(endpoints))
	classes := make([]string, len(endpoints))
	var g errgroup.Group
	g.SetLimit(endpointReads)
	for i, e := range endpoints {
		g.Go(func() error {
			results[i], classes[i] = s.readEndpoint(rctx, e.URL)
			return nil
		})
	}
	_ = g.Wait() // each read reports its own class, never an error
	cancel()
	if ctx.Err() != nil {
		return nil
	}

	readAt := s.Now().UTC()
	read := store.LayerRead{OrganisationID: p.OrganisationID, ProjectID: p.ID, Layer: store.LayerWorkers, Every: s.Every, ReadAt: readAt}
	reads := make([]store.WorkerEndpointRead, 0, len(endpoints))
	var samples []store.WorkerSample
	for i, e := range endpoints {
		er := store.WorkerEndpointRead{EndpointID: e.ID, ReadAt: readAt, Every: s.Every, OK: classes[i] == "", ErrorClass: classes[i]}
		reads = append(reads, er)
		if !er.OK {
			s.Log.Warn("scrape: worker endpoint read failed", "organisation", p.OrganisationID, "project", p.ID, "endpoint", e.ID, "class", er.ErrorClass)
			if read.ErrorClass == "" {
				read.ErrorClass = er.ErrorClass // the first endpoint's class names the layer's failure
			}
			continue
		}
		read.OK = true
		for _, m := range results[i] {
			samples = append(samples, store.WorkerSample{
				OrganisationID: p.OrganisationID, ProjectID: p.ID, ReadAt: readAt, EndpointID: e.ID,
				Nodename: m.nodename, Load: m.load, ActiveJobs: m.activeJobs,
			})
		}
	}
	if read.OK {
		read.ErrorClass = ""
	}
	sctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	if err := s.Samples.StoreWorkerRead(sctx, read, reads, samples); err != nil {
		return fmt.Errorf("store worker read: %w", err)
	}
	if s.Changed != nil {
		s.Changed(ctx, read.OrganisationID, read.ProjectID)
	}
	return nil
}

// workerMetrics is what one endpoint reported for one worker.
type workerMetrics struct {
	nodename   string
	load       *float64
	activeJobs *int64
}

// readEndpoint reads one endpoint and returns its workers, or the class of why it could not be read.
// Neither the URL nor the transport's error text is returned or logged: the URL can carry a
// credential, and Go's URL errors repeat it.
func (s *WorkerScraper) readEndpoint(ctx context.Context, rawURL string) ([]workerMetrics, string) {
	cctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(cctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, store.ErrorOther
	}
	// The classic text format, which LiveKit Agents' prometheus_client serves by default.
	req.Header.Set("Accept", string(expfmt.NewFormat(expfmt.TypeTextPlain)))
	res, err := s.Client.Do(req)
	if err != nil {
		return nil, store.ErrorUnreachable
	}
	defer func() { _ = res.Body.Close() }()
	switch {
	case res.StatusCode == http.StatusUnauthorized:
		return nil, store.ErrorUnauthenticated
	case res.StatusCode == http.StatusForbidden:
		return nil, store.ErrorPermissionDenied
	case res.StatusCode == http.StatusBadGateway || res.StatusCode == http.StatusServiceUnavailable || res.StatusCode == http.StatusGatewayTimeout:
		return nil, store.ErrorUnreachable
	case res.StatusCode < 200 || res.StatusCode > 299:
		return nil, store.ErrorOther
	}
	body, err := readCapped(res.Body, maxMetricsBody)
	if err != nil {
		if cctx.Err() != nil {
			return nil, store.ErrorUnreachable
		}
		return nil, store.ErrorOther // too large, or cut off mid-body
	}
	workers, err := parseWorkerMetrics(bytes.NewReader(body), expfmt.ResponseFormat(res.Header))
	if err != nil {
		if errors.Is(err, errNoAgentMetrics) {
			return nil, store.ErrorNotSupported
		}
		if cctx.Err() != nil {
			return nil, store.ErrorUnreachable
		}
		return nil, store.ErrorOther
	}
	return workers, ""
}

// errBodyTooLarge means an endpoint answered with more than the read takes in.
var errBodyTooLarge = errors.New("metrics answer over the size limit")

// readCapped reads at most limit bytes, and fails when there are more instead of returning the
// first limit.
func readCapped(r io.Reader, limit int64) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, limit+1))
	if err != nil {
		return nil, fmt.Errorf("read metrics: %w", err)
	}
	if int64(len(b)) > limit {
		return nil, errBodyTooLarge
	}
	return b, nil
}

// errTooManyWorkers means an endpoint reported more workers than one endpoint may.
var errTooManyWorkers = fmt.Errorf("more than %d workers on one endpoint", maxEndpointWorkers)

// errNoAgentMetrics means an endpoint answered without LiveKit Agents' worker metrics.
var errNoAgentMetrics = errors.New("no LiveKit Agents worker metrics")

// parseWorkerMetrics reads lk_agents_worker_load and lk_agents_active_job_count per nodename with
// Prometheus' own decoder. A worker that exports one of the two has the other null; a NaN or
// infinite value is null too, and so is a job count that is negative, fractional or past 2^63.
func parseWorkerMetrics(r io.Reader, format expfmt.Format) ([]workerMetrics, error) {
	dec := expfmt.NewDecoder(r, format)
	byNode := map[string]*workerMetrics{}
	var order []string
	seen := false
	node := func(name string) (*workerMetrics, error) {
		w, ok := byNode[name]
		if !ok {
			if len(byNode) >= maxEndpointWorkers {
				return nil, errTooManyWorkers
			}
			w = &workerMetrics{nodename: name}
			byNode[name] = w
			order = append(order, name)
		}
		return w, nil
	}
	for {
		var mf dto.MetricFamily
		err := dec.Decode(&mf)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("decode metrics: %w", err)
		}
		name := mf.GetName()
		if name != metricWorkerLoad && name != metricActiveJobs {
			continue
		}
		seen = true
		for _, m := range mf.GetMetric() {
			nodename := ""
			for _, l := range m.GetLabel() {
				if l.GetName() == labelNodename {
					nodename = l.GetValue()
				}
			}
			if nodename == "" {
				continue // LiveKit Agents always labels its gauges; an unlabelled series names no worker
			}
			v, ok := gaugeValue(m)
			w, err := node(nodename)
			if err != nil {
				return nil, err
			}
			if !ok {
				continue
			}
			if name == metricWorkerLoad {
				w.load = &v
			} else if v >= 0 && v < math.Exp2(63) && v == math.Trunc(v) {
				// A count below zero, or past what int64 holds, is not a job count.
				n := int64(v)
				w.activeJobs = &n
			}
		}
	}
	if !seen {
		return nil, errNoAgentMetrics
	}
	slices.Sort(order)
	out := make([]workerMetrics, 0, len(order))
	for _, n := range order {
		out = append(out, *byNode[n])
	}
	return out, nil
}

// gaugeValue reads a gauge, or an untyped sample when the endpoint sent no TYPE line.
func gaugeValue(m *dto.Metric) (float64, bool) {
	var v float64
	switch {
	case m.GetGauge() != nil:
		v = m.GetGauge().GetValue()
	case m.GetUntyped() != nil:
		v = m.GetUntyped().GetValue()
	default:
		return 0, false
	}
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0, false
	}
	return v, true
}
