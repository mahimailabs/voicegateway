package app

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"time"

	"github.com/go-logr/logr"
	lklogger "github.com/livekit/protocol/logger"
	lksdk "github.com/livekit/server-sdk-go/v2"

	"github.com/mahimailabs/voicegateway/internal/capacity"
	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// CapacityInput is one `voicegateway capacity` run: the agent, where it is, and how far to go.
type CapacityInput struct {
	LiveKitURL, APIKey, APISecret string
	Agent                         string
	MaxStep                       int
	JoinMs                        float64
	Refine                        bool
	OutDir                        string
	Version                       string
}

// CapacityFiles are the report files a run writes, by name.
var CapacityFiles = []string{"capacity-report.html", "capacity-report.md", "capacity-report.json"}

// RunCapacity runs one capacity test with no database (docs/specs/capacity-command.md): the
// worker's runner on an in-memory run. It prints each step as it ends to out, writes the report
// files to in.OutDir and returns the report. A first stop (Ctrl-C) ends the run as stopped and
// deletes every room it created; the report is still written.
func RunCapacity(ctx context.Context, in CapacityInput, stop <-chan struct{}, out io.Writer) (capacity.Report, error) {
	if err := os.MkdirAll(in.OutDir, 0o750); err != nil {
		return capacity.Report{}, fmt.Errorf("report folder: %w", err)
	}
	id, err := runID()
	if err != nil {
		return capacity.Report{}, err
	}
	// The callers join through server-sdk-go, whose WebRTC stack logs every connection step and
	// harmless setup errors (mDNS on a host without IPv6). The run's own events say what a call saw,
	// so the terminal shows only the steps.
	lksdk.SetLogger(lklogger.LogRLogger(logr.Discard()))
	local := capacity.NewLocal(id, in.Agent, in.MaxStep, time.Now())
	local.LiveKitURL, local.APIKey, local.APISecret, local.JoinMs = in.LiveKitURL, in.APIKey, in.APISecret, in.JoinMs
	local.OnStep = func(s store.CapacityStep) {
		if s.Outcome == store.StepRunning {
			return
		}
		tail := capacity.TailOf(s.AnswerSeconds)
		calls := "calls"
		if s.Step == 1 {
			calls = "call "
		}
		_, _ = fmt.Fprintf(out, "  %3d %s  %-12s got the agent %d, refused %d, dropped %d · median %s, p95 %s\n",
			s.Step, calls, s.Outcome, s.Joined, s.Pending, s.Failed, short(tail.Median), short(tail.P95))
	}
	go func() {
		select {
		case <-stop:
			_, _ = fmt.Fprintln(out, "  stopping: hanging up every test call and deleting its rooms")
			local.Stop()
		case <-ctx.Done():
		}
	}()
	r := &capacity.Runner{
		Runs: local, Projects: local, Dial: capacity.NewDial(scrape.NewWorkerClient(false)),
		CPU: capacity.HostCPU, ID: "local", Log: slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError})), Now: time.Now,
		Poll: time.Second, Beat: time.Second, Lost: 30 * time.Second, Unclaimed: time.Minute,
		StepFor: time.Minute, BusyEvery: 10 * time.Second, Timeout: 10 * time.Second,
		Refine: in.Refine, Cooldown: 20 * time.Second,
	}
	if err := r.Tick(ctx); err != nil {
		return capacity.Report{}, fmt.Errorf("capacity test: %w", err)
	}
	rep := capacity.NewReport(local.Run(), in.LiveKitURL, in.Version)
	if err := WriteCapacityReport(rep, in.OutDir); err != nil {
		return rep, err
	}
	return rep, nil
}

// WriteCapacityReport writes the report's HTML, Markdown and JSON files into dir.
func WriteCapacityReport(rep capacity.Report, dir string) error {
	html, err := rep.HTML()
	if err != nil {
		return err
	}
	js, err := rep.JSON()
	if err != nil {
		return err
	}
	for name, body := range map[string][]byte{CapacityFiles[0]: html, CapacityFiles[1]: rep.Markdown(), CapacityFiles[2]: js} {
		if err := os.WriteFile(filepath.Join(dir, name), body, 0o644); err != nil { //nolint:gosec // a report the person asked for, to open and share
			return fmt.Errorf("write %s: %w", name, err)
		}
	}
	return nil
}

// runID is a random UUID-shaped id; a test room's name carries its first 8 hex digits.
func runID() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", errors.Join(errors.New("capacity run id"), err)
	}
	h := hex.EncodeToString(b)
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:], nil
}

func short(v *float64) string {
	if v == nil {
		return "unknown"
	}
	return fmt.Sprintf("%.2f s", *v)
}
