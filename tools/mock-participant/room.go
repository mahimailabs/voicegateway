package main

import (
	"context"
	"fmt"
	"time"

	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

// opusCapability is the codec every published track advertises. It must match
// what the remote registered or the answer is rejected with "codec is not
// supported by remote", which surfaces as a join that times out several
// seconds later rather than as a codec error.
//
// CHANNELS IS 2 EVEN THOUGH THE TONE IS MONO. That is not a mismatch: RFC 7587
// specifies the Opus RTP payload format as always declaring two channels, and
// mono content is signalled inside the stream (Opus packets are self-describing
// about channel count) rather than by changing the rtpmap. Declaring 1 here
// produces an rtpmap no WebRTC endpoint offers. The fmtp line matches pion's
// own default registration so the two compare equal.
var opusCapability = webrtc.RTPCodecCapability{
	MimeType:    webrtc.MimeTypeOpus,
	ClockRate:   48000,
	Channels:    2,
	SDPFmtpLine: "minptime=10;useinbandfec=1",
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
func JoinAndPublish(
	ctx context.Context, a Assignment, stats *CallStats, logf func(string, ...any),
) error {
	frames, err := ToneFrames()
	if err != nil {
		return fmt.Errorf("tone: %w", err)
	}

	track, err := lksdk.NewLocalSampleTrack(opusCapability)
	if err != nil {
		return fmt.Errorf("create track: %w", err)
	}

	// The drain is wired BEFORE the join, so a track subscribed during the join
	// handshake is read from the moment it exists. Wiring it afterwards leaves a
	// window where pion is buffering a track nobody is reading.
	callback := lksdk.NewRoomCallback()
	callback.OnTrackSubscribed = func(
		track *webrtc.TrackRemote, _ *lksdk.RemoteTrackPublication, rp *lksdk.RemoteParticipant,
	) {
		stats.TracksSubscribed.Add(1)
		if logf != nil {
			logf("subscribed to %s from %s, draining", track.Kind(), rp.Identity())
		}
		// One goroutine per track. It ends when the track does, which is what
		// ReadRTP reports as an error.
		go drainRTP(track, stats)
	}

	// WithSinglePeerConnection is a PRECONDITION of WithTrack, not a tuning
	// option: queuing a track for the join request without it is rejected at
	// join time with ErrPublishRequiresSinglePC. The two travel together, and
	// dropping either one silently turns this back into a publish that costs a
	// second round trip on every call's answer path.
	room, err := lksdk.ConnectToRoomWithToken(
		a.URL, a.Token, callback,
		lksdk.WithSinglePeerConnection(),
		lksdk.WithTrack(track, &lksdk.TrackPublicationOptions{Name: "mock-audio"}),
	)
	if err != nil {
		return fmt.Errorf("join %q: %w", a.RoomName, err)
	}
	defer room.Disconnect()
	stats.markJoined(time.Now())
	stats.setParticipantSID(room.LocalParticipant.SID())

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
