package main

import (
	"context"
	"fmt"
	"time"

	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

// opusCapability is the codec every published track advertises. 48kHz mono is
// what tone.ogg was encoded at, and what livekit-sip expects to subscribe to.
var opusCapability = webrtc.RTPCodecCapability{
	MimeType:  webrtc.MimeTypeOpus,
	ClockRate: 48000,
	Channels:  1,
}

// JoinAndPublish joins the assigned room with an audio track ALREADY ATTACHED
// and streams the shared tone until ctx is cancelled.
//
// The track goes in through lksdk.WithTrack, which makes publishing part of the
// join rather than a second round-trip after it. That ordering is the point:
// livekit-sip does not send 200 OK until it has subscribed to an audio track, so
// a publish that waits for the join to complete first adds its whole round trip
// to every call's answer latency, and at 500 concurrent that shows up directly
// in the establishment rate.
func JoinAndPublish(ctx context.Context, a Assignment, logf func(string, ...any)) error {
	frames, err := ToneFrames()
	if err != nil {
		return fmt.Errorf("tone: %w", err)
	}

	track, err := lksdk.NewLocalSampleTrack(opusCapability)
	if err != nil {
		return fmt.Errorf("create track: %w", err)
	}

	room, err := lksdk.ConnectToRoomWithToken(
		a.URL, a.Token, lksdk.NewRoomCallback(),
		lksdk.WithTrack(track, &lksdk.TrackPublicationOptions{Name: "mock-audio"}),
	)
	if err != nil {
		return fmt.Errorf("join %q: %w", a.RoomName, err)
	}
	defer room.Disconnect()

	if logf != nil {
		logf("joined room %q as %s, publishing %d frames of tone",
			a.RoomName, room.LocalParticipant.Identity(), len(frames))
	}
	return streamTone(ctx, track, frames)
}

// streamTone writes the shared frames on a fixed ticker, looping.
//
// No allocation and no encoding in the loop: every frame is a slice of the
// shared, parsed-once backing array, handed straight to WriteSample. That is
// what keeps a call's cost flat as concurrency rises.
//
// Paced by a ticker rather than by sleeping for the frame duration, so a slow
// WriteSample does not accumulate drift across a long call.
func streamTone(ctx context.Context, track *lksdk.LocalTrack, frames [][]byte) error {
	ticker := time.NewTicker(ToneFrameDuration)
	defer ticker.Stop()
	i := 0
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			frame := frames[i%len(frames)]
			i++
			// Duration is the frame size the tone was encoded at, not a measured
			// value: every frame in the set is exactly one Opus frame.
			err := track.WriteSample(
				media.Sample{Data: frame, Duration: ToneFrameDuration}, nil,
			)
			if err != nil {
				if ctx.Err() != nil {
					return nil
				}
				return fmt.Errorf("write sample: %w", err)
			}
		}
	}
}
