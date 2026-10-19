package ingest

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
)

type quotaResult struct {
	allowed bool
	warning int
	err     error
}

func (q quotaResult) Check(context.Context, string) (bool, int, error) {
	return q.allowed, q.warning, q.err
}

func TestQuotaRefusalAndRetry(t *testing.T) {
	for _, tt := range []struct {
		name    string
		q       Quota
		status  int
		allowed bool
	}{
		{"self hosted", nil, 200, true},
		{"allowed", quotaResult{allowed: true, warning: 80}, 200, true},
		{"cap", quotaResult{warning: 100}, 402, false},
		{"database failure", quotaResult{err: errors.New("down")}, 503, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			ok := checkQuota(w, httptest.NewRequest("POST", "/", nil), tt.q, "org")
			if ok != tt.allowed || w.Code != tt.status {
				t.Fatalf("got %v %d", ok, w.Code)
			}
		})
	}
}

type quotaAppender struct{ n atomic.Int64 }

func (a *quotaAppender) Append(context.Context, event.Envelope) (string, error) {
	a.n.Add(1)
	return "1", nil
}

func TestExhaustedQuotaNeverAppendsAuthenticatedUploads(t *testing.T) {
	appender := &quotaAppender{}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	u := &Uploads{Quota: quotaResult{warning: 100}, Projects: fixedProject{}, Stream: appender, Log: logger, Now: time.Now}
	token := bearer(t, testKey, testSecret, true)
	var wg sync.WaitGroup
	for range 20 {
		wg.Go(func() {
			rec := send(u.OTLP(event.KindAgentTraces), token, "", "application/x-protobuf", []byte{10, 0})
			if rec.Code != 402 {
				t.Errorf("upload status %d", rec.Code)
			}
		})
	}
	wg.Wait()
	w := &Webhooks{Quota: quotaResult{warning: 100}, Projects: fixedProject{}, Stream: appender, Log: logger, Now: time.Now}
	rec := post(w, sign(t, testKey, testSecret, roomStarted), roomStarted)
	if rec.Code != 402 {
		t.Fatalf("webhook status %d", rec.Code)
	}
	if appender.n.Load() != 0 {
		t.Fatal("exhausted telemetry was appended")
	}
}
