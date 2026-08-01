package main

import (
	"os"
	"strings"
	"testing"
)

// TestWithTrackIsPairedWithSinglePeerConnection guards a precondition that no
// unit test can exercise, because it is only enforced when Join talks to a real
// server: queuing a track via WithTrack without WithSinglePeerConnection is
// rejected at join time with ErrPublishRequiresSinglePC.
//
// This package shipped without the pairing and compiled, vetted and unit-tested
// clean. It failed on the first real call, at the join, after the worker
// handshake and job assignment had both succeeded. A source-level check is a
// blunt instrument, but it is the only one that fires without a live LiveKit,
// and dropping the option is a one-line edit that otherwise looks harmless.
func TestWithTrackIsPairedWithSinglePeerConnection(t *testing.T) {
	source, err := os.ReadFile("room.go")
	if err != nil {
		t.Fatalf("read room.go: %v", err)
	}
	text := string(source)
	if !strings.Contains(text, "lksdk.WithTrack(") {
		t.Fatal("room.go no longer calls WithTrack; this guard needs rewriting")
	}
	if !strings.Contains(text, "lksdk.WithSinglePeerConnection()") {
		t.Error(
			"room.go queues a track with WithTrack but never enables " +
				"WithSinglePeerConnection; the join is rejected with " +
				"ErrPublishRequiresSinglePC against a real server",
		)
	}
}
