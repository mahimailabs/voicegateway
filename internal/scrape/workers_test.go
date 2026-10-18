package scrape

import (
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/common/expfmt"
)

// What LiveKit Agents' prometheus_client serves, in the classic text format: one gauge of each per
// nodename. A missing gauge, NaN or a non-integer job count is null, never zero.
func TestWorkerMetricsUnreportedStaysNull(t *testing.T) {
	text := expfmt.NewFormat(expfmt.TypeTextPlain)
	tests := []struct {
		name string
		body string
		want string
		err  error
	}{
		{
			name: "both gauges for one worker",
			body: "# TYPE lk_agents_worker_load gauge\nlk_agents_worker_load{nodename=\"mac\"} 0.4116\n" +
				"# TYPE lk_agents_active_job_count gauge\nlk_agents_active_job_count{nodename=\"mac\"} 4\n",
			want: "[mac load=0.4116 jobs=4]",
		},
		{
			name: "two workers on one endpoint, load only",
			body: "lk_agents_worker_load{nodename=\"b\"} 0.1\nlk_agents_worker_load{nodename=\"a\"} 0.2\n",
			want: "[a load=0.2 jobs=null b load=0.1 jobs=null]",
		},
		{
			name: "jobs only, load NaN",
			body: "lk_agents_worker_load{nodename=\"w\"} NaN\nlk_agents_active_job_count{nodename=\"w\"} 0\n",
			want: "[w load=null jobs=0]",
		},
		{
			name: "a job count that is not a count",
			body: "lk_agents_active_job_count{nodename=\"w\"} 1.5\n",
			want: "[w load=null jobs=null]",
		},
		{
			name: "other metrics around them",
			body: "process_cpu_seconds_total 12\nlk_agents_child_process_count{nodename=\"w\"} 3\nlk_agents_worker_load{nodename=\"w\"} 0\n",
			want: "[w load=0 jobs=null]",
		},
		{
			name: "a negative job count",
			body: "lk_agents_active_job_count{nodename=\"w\"} -1\n",
			want: "[w load=null jobs=null]",
		},
		{
			name: "a job count past int64",
			body: "lk_agents_active_job_count{nodename=\"w\"} 9.223372036854775808e18\n",
			want: "[w load=null jobs=null]",
		},
		{
			name: "more workers than one endpoint may report",
			body: series(maxEndpointWorkers + 1),
			err:  errTooManyWorkers,
		},
		{
			name: "no LiveKit Agents metrics",
			body: "process_cpu_seconds_total 12\n",
			err:  errNoAgentMetrics,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := parseWorkerMetrics(strings.NewReader(tt.body), text)
			if tt.err != nil {
				if !errors.Is(err, tt.err) {
					t.Fatalf("err = %v, want %v", err, tt.err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			var parts []string
			for _, w := range got {
				load, jobs := "null", "null"
				if w.load != nil {
					load = fmt.Sprint(*w.load)
				}
				if w.activeJobs != nil {
					jobs = fmt.Sprint(*w.activeJobs)
				}
				parts = append(parts, fmt.Sprintf("%s load=%s jobs=%s", w.nodename, load, jobs))
			}
			if s := "[" + strings.Join(parts, " ") + "]"; s != tt.want {
				t.Errorf("workers = %s, want %s", s, tt.want)
			}
		})
	}
}

// series is n workers' load gauges on one endpoint.
func series(n int) string {
	var b strings.Builder
	for i := range n {
		fmt.Fprintf(&b, "lk_agents_worker_load{nodename=\"w%d\"} 0.1\n", i)
	}
	return b.String()
}

func TestWorkerSeriesCap(t *testing.T) {
	got, err := parseWorkerMetrics(strings.NewReader(series(maxEndpointWorkers)), expfmt.NewFormat(expfmt.TypeTextPlain))
	if err != nil || len(got) != maxEndpointWorkers {
		t.Errorf("%d workers: got %d, %v; want all of them", maxEndpointWorkers, len(got), err)
	}
}

// An answer over the size limit is a failed read with class error, never a truncated read kept as
// good; one at the limit is read.
func TestWorkerBodyCap(t *testing.T) {
	for _, tt := range []struct {
		name string
		size int
		want string
	}{
		{"at the limit", maxMetricsBody, ""},
		{"one byte over", maxMetricsBody + 1, "error"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			line := "lk_agents_worker_load{nodename=\"w\"} 0.5\n"
			pad := tt.size - len(line)
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "text/plain; version=0.0.4")
				// Comment lines pad the answer to its size; the gauge comes last, so a truncated read
				// would lose it.
				for pad > 0 {
					n := min(pad, 4096)
					_, _ = fmt.Fprint(w, "#"+strings.Repeat(" ", n-2)+"\n")
					pad -= n
				}
				_, _ = fmt.Fprint(w, line)
			}))
			defer srv.Close()
			s := &WorkerScraper{Client: NewWorkerClient(), Timeout: 5 * time.Second}
			workers, class := s.readEndpoint(t.Context(), srv.URL)
			if class != tt.want {
				t.Fatalf("class = %q, want %q", class, tt.want)
			}
			if tt.want == "" && (len(workers) != 1 || workers[0].load == nil) {
				t.Errorf("workers = %+v, want the one gauge", workers)
			}
		})
	}
}

// The endpoint client follows no redirect: the second host is never called, so a URL's credentials
// never reach it, and the read fails with class error.
func TestWorkerClientRefusesRedirects(t *testing.T) {
	var reached atomic.Int64
	var forwarded atomic.Value
	second := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached.Add(1)
		forwarded.Store(r.Header.Get("Authorization"))
		_, _ = fmt.Fprint(w, "lk_agents_worker_load{nodename=\"w\"} 0.5\n")
	}))
	defer second.Close()
	var sentAuth atomic.Bool
	first := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if u, p, ok := r.BasicAuth(); ok && u == "scraper" && p == "metrics-pass" {
			sentAuth.Store(true)
		}
		http.Redirect(w, r, second.URL+"/metrics", http.StatusFound)
	}))
	defer first.Close()

	s := &WorkerScraper{Client: NewWorkerClient(), Timeout: 5 * time.Second}
	url := strings.Replace(first.URL, "http://", "http://scraper:metrics-pass@", 1) + "/metrics"
	if _, class := s.readEndpoint(t.Context(), url); class != "error" {
		t.Errorf("class = %q, want error for a redirect", class)
	}
	if !sentAuth.Load() {
		t.Error("the listed endpoint did not get the URL's credentials")
	}
	if n := reached.Load(); n != 0 {
		t.Errorf("the redirect target was called %d times (Authorization %q), want never", n, forwarded.Load())
	}
}
