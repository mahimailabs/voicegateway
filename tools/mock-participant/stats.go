package main

import (
	"sync/atomic"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtp"
)

// CallStats is what one call actually did, counted as it happens.
//
// Every field is atomic because the drain runs one goroutine per subscribed
// track while the reporter reads them on a timer. These are the numbers MOCK4
// posts as observations, so they are counts of things that happened rather than
// anything derived: a derived number here would be a measurement nobody took.
type CallStats struct {
	RTPPacketsReceived atomic.Uint64
	RTPBytesReceived   atomic.Uint64
	TracksSubscribed   atomic.Int64

	// Unix nanoseconds. Zero means it has not happened.
	joinedAtNanos    atomic.Int64
	firstPacketNanos atomic.Int64

	// The SID the server assigned this participant, known only after the join.
	participantSID atomic.Value
}

func (c *CallStats) setParticipantSID(sid string) { c.participantSID.Store(sid) }

// ParticipantSID is the SID the server assigned, or "" before the join.
func (c *CallStats) ParticipantSID() string {
	if v, ok := c.participantSID.Load().(string); ok {
		return v
	}
	return ""
}

func (c *CallStats) markJoined(t time.Time) { c.joinedAtNanos.Store(t.UnixNano()) }

// JoinedAt is when the room join completed, or the zero time if it has not.
func (c *CallStats) JoinedAt() time.Time { return nanosToTime(c.joinedAtNanos.Load()) }

// FirstPacketAt is when the first RTP packet arrived on any subscribed track.
//
// This is the bidirectional-media evidence: a call where audio only ever flowed
// one way has a join time and no first packet, and the two being separate fields
// is what keeps that distinguishable from a call that never joined.
func (c *CallStats) FirstPacketAt() time.Time { return nanosToTime(c.firstPacketNanos.Load()) }

func nanosToTime(n int64) time.Time {
	if n == 0 {
		return time.Time{}
	}
	return time.Unix(0, n)
}

// rtpReader is the part of *webrtc.TrackRemote the drain needs.
//
// Narrowed to one method so the drain can be tested against a fake: constructing
// a real TrackRemote requires a live peer connection, and a drain that is only
// exercised against a live server is a drain nobody tests.
type rtpReader interface {
	ReadRTP() (*rtp.Packet, interceptor.Attributes, error)
}

// drainRTP reads a subscribed track until it ends, counting what arrives.
//
// THE READING IS NOT OPTIONAL. pion buffers incoming RTP per track and a
// consumer that never reads lets those buffers grow until they are dropped, so a
// worker that subscribes and ignores the track degrades the very media quality
// the run is measuring. Counting is the second reason: the count is the evidence
// that audio flowed both ways.
//
// Returns when the track ends, which is what ReadRTP reports as an error. That
// is the normal end of a call, not a failure, so it is not logged as one.
func drainRTP(r rtpReader, stats *CallStats) {
	for {
		packet, _, err := r.ReadRTP()
		if err != nil {
			return
		}
		if packet == nil {
			continue
		}
		stats.RTPPacketsReceived.Add(1)
		stats.RTPBytesReceived.Add(uint64(len(packet.Payload)))
		// CompareAndSwap so only the first packet records the time, without a
		// lock on the hot path.
		stats.firstPacketNanos.CompareAndSwap(0, time.Now().UnixNano())
	}
}
