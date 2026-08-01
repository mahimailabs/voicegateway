package main

import (
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/pion/webrtc/v4"
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

// TestOpusCapabilityNegotiatesWithAWebRTCPeer runs a full offer/answer against
// a second peer connection and asserts the published capability survives it.
//
// This reproduces the production failure exactly. Declaring one channel yields
// "unable to start track, codec is not supported by remote" from
// SetRemoteDescription, which is the same string a real SFU returned. Nothing
// cheaper catches it: NewTrackLocalStaticSample accepts the bad capability,
// AddTrack accepts it, and the generated OFFER still advertises opus/48000/2
// because the offer lists the media engine's registered codecs rather than the
// track's. The mismatch only appears when the ANSWER is applied.
//
// The visible symptom on a real server is not a codec error either: the peer
// connection never completes and the join fails seconds later with "could not
// connect after timeout", which points at the network instead of the codec.
func TestOpusCapabilityNegotiatesWithAWebRTCPeer(t *testing.T) {
	if err := negotiateWith(opusCapability); err != nil {
		t.Fatalf("a standard WebRTC peer rejected opusCapability: %v", err)
	}
}

// TestMonoOpusCapabilityIsRejected proves the test above is not vacuous.
//
// RFC 7587 declares the Opus RTP payload format with two channels whatever the
// content is; mono is signalled inside the stream. Reading the channel count
// off the tone (which IS mono) is what produced the bug, so the wrong value is
// pinned here as rejected rather than merely described in a comment.
func TestMonoOpusCapabilityIsRejected(t *testing.T) {
	mono := opusCapability
	mono.Channels = 1
	err := negotiateWith(mono)
	if err == nil {
		t.Fatal("opus/48000/1 negotiated cleanly; this guard no longer discriminates")
	}
	if !strings.Contains(err.Error(), "codec is not supported by remote") {
		t.Errorf("rejected for the wrong reason: %v", err)
	}
}

// negotiateWith runs a complete offer/answer between two peer connections with
// a track built from cap, returning the error the exchange produced.
func negotiateWith(capability webrtc.RTPCodecCapability) error {
	offerer, err := webrtc.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return err
	}
	defer offerer.Close()
	answerer, err := webrtc.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		return err
	}
	defer answerer.Close()

	track, err := webrtc.NewTrackLocalStaticSample(capability, "audio", "mock")
	if err != nil {
		return fmt.Errorf("new track: %w", err)
	}
	if _, err = offerer.AddTrack(track); err != nil {
		return fmt.Errorf("add track: %w", err)
	}
	offer, err := offerer.CreateOffer(nil)
	if err != nil {
		return err
	}
	if err = offerer.SetLocalDescription(offer); err != nil {
		return err
	}
	if err = answerer.SetRemoteDescription(offer); err != nil {
		return fmt.Errorf("answerer SetRemoteDescription: %w", err)
	}
	answer, err := answerer.CreateAnswer(nil)
	if err != nil {
		return err
	}
	if err = answerer.SetLocalDescription(answer); err != nil {
		return err
	}
	// Where "codec is not supported by remote" fires.
	if err = offerer.SetRemoteDescription(answer); err != nil {
		return fmt.Errorf("offerer SetRemoteDescription: %w", err)
	}
	return nil
}
