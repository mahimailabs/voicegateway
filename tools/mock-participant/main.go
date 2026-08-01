package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	var (
		serverURL = flag.String("url", os.Getenv("LIVEKIT_URL"), "LiveKit server URL (ws://, wss://, http:// or https://)")
		apiKey    = flag.String("api-key", os.Getenv("LIVEKIT_API_KEY"), "LiveKit API key")
		apiSecret = flag.String("api-secret", os.Getenv("LIVEKIT_API_SECRET"), "LiveKit API secret")
		agentName = flag.String("agent-name", "mock-participant", "name this worker registers under; dispatch matches on it")
		maxJobs   = flag.Int("max-jobs", 0, "declared capacity used to compute reported load; 0 leaves load undeclared at 0")
		collector = flag.String("collector-url", os.Getenv("VOICEGW_COLLECTOR_URL"), "VoiceGateway base URL for call observations; empty disables reporting")
		collKey   = flag.String("collector-key", os.Getenv("VOICEGW_API_KEY"), "API key for the collector; needs the write scope")
		runID     = flag.String("run-id", "", "load-run id stamped on every observation, so one run's calls can be found together")
		project   = flag.String("project", "", "project to scope observations to")
		quiet     = flag.Bool("quiet", false, "suppress progress output")
	)
	flag.Parse()

	logf := log.Printf
	if *quiet {
		logf = func(string, ...any) {}
	}

	reporter := &Reporter{
		BaseURL: *collector,
		APIKey:  *collKey,
		Project: *project,
		Logf:    logf,
	}
	if !reporter.Enabled() {
		logf("no -collector-url set: call observations will not be filed")
	}

	w := &Worker{
		URL:       *serverURL,
		APIKey:    *apiKey,
		APISecret: *apiSecret,
		AgentName: *agentName,
		Logf:      logf,
		MaxJobs:   *maxJobs,
		OnAssignment: func(ctx context.Context, a Assignment) error {
			logf("job %s joining room %q at %s", a.JobID, a.RoomName, a.URL)
			stats := &CallStats{}
			err := JoinAndPublish(ctx, a, stats, logf)
			// The counts are the bidirectional-media evidence, so they are
			// reported whether the call ended cleanly or not.
			logf("job %s ended: %d rtp packets, %d bytes, %d tracks",
				a.JobID, stats.RTPPacketsReceived.Load(),
				stats.RTPBytesReceived.Load(), stats.TracksSubscribed.Load())

			// After the call, never during it: this worker is on the SIP answer
			// path and a slow collector must not become call latency. A detached
			// context so a cancelled run still files what it measured.
			reportCtx, cancelReport := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancelReport()
			if rerr := reporter.Report(reportCtx, a, stats, stats.ParticipantSID(), *runID); rerr != nil {
				// Swallowed: losing an observation is a gap in evidence, while
				// failing the call over it would be a gap in the run.
				logf("job %s observation not filed: %v", a.JobID, rerr)
			}
			return err
		},
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := w.Connect(ctx); err != nil {
		log.Fatalf("connect: %v", err)
	}
	defer func() { _ = w.Close() }()
	logf("registered with %s as %q, waiting for jobs", *serverURL, *agentName)

	if err := w.Run(ctx); err != nil {
		log.Fatalf("run: %v", err)
	}
	// A clean shutdown gets a moment for the close frame to leave.
	time.Sleep(50 * time.Millisecond)
	logf("shut down")
}
