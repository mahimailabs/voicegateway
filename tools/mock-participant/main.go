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
		quiet     = flag.Bool("quiet", false, "suppress progress output")
	)
	flag.Parse()

	logf := log.Printf
	if *quiet {
		logf = func(string, ...any) {}
	}

	w := &Worker{
		URL:       *serverURL,
		APIKey:    *apiKey,
		APISecret: *apiSecret,
		AgentName: *agentName,
		Logf:      logf,
		OnAssignment: func(_ context.Context, a Assignment) error {
			// Joining the room and publishing audio land here. Until then this
			// reports what it was given, which is what makes the handshake
			// verifiable on its own.
			logf("job %s ready to join room %q at %s (token %d bytes)",
				a.JobID, a.RoomName, a.URL, len(a.Token))
			return nil
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
