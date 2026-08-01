package main

import (
	"context"
	"errors"
	"io"
	"sync"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	"github.com/pion/interceptor"
	"github.com/pion/rtp"
)

// fakeTrack hands out a fixed number of packets and then ends, the way a real
// track does when the call hangs up.
type fakeTrack struct {
	mu        sync.Mutex
	remaining int
	payload   []byte
	blockFor  time.Duration
	reads     int
}

func (f *fakeTrack) ReadRTP() (*rtp.Packet, interceptor.Attributes, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.blockFor > 0 {
		time.Sleep(f.blockFor)
	}
	f.reads++
	if f.remaining <= 0 {
		// What pion reports when the track ends. A normal hangup, not a failure.
		return nil, nil, io.EOF
	}
	f.remaining--
	return &rtp.Packet{Payload: f.payload}, nil, nil
}

func (f *fakeTrack) readCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.reads
}

func TestTheDrainCountsEveryPacketAndItsBytes(t *testing.T) {
	track := &fakeTrack{remaining: 250, payload: make([]byte, 60)}
	stats := &CallStats{}
	drainRTP(track, stats)

	if got := stats.RTPPacketsReceived.Load(); got != 250 {
		t.Errorf("packets = %d, want 250", got)
	}
	if got := stats.RTPBytesReceived.Load(); got != 250*60 {
		t.Errorf("bytes = %d, want %d", got, 250*60)
	}
}

func TestTheDrainKeepsReadingUntilTheTrackEnds(t *testing.T) {
	// The reading is not optional: pion buffers incoming RTP per track, and a
	// consumer that stops early lets those buffers grow until packets are
	// dropped, degrading the media quality the run is measuring.
	track := &fakeTrack{remaining: 1000, payload: make([]byte, 1)}
	drainRTP(track, &CallStats{})
	// 1000 packets plus the read that returned EOF.
	if got := track.readCount(); got != 1001 {
		t.Errorf("read %d times, want 1001 (every packet plus the EOF)", got)
	}
}

func TestTheDrainReturnsOnTrackEndRatherThanSpinning(t *testing.T) {
	done := make(chan struct{})
	go func() {
		drainRTP(&fakeTrack{remaining: 0}, &CallStats{})
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("drain did not return when the track ended")
	}
}

type errTrack struct{ err error }

func (e *errTrack) ReadRTP() (*rtp.Packet, interceptor.Attributes, error) {
	return nil, nil, e.err
}

func TestAnyReadErrorEndsTheDrain(t *testing.T) {
	for _, err := range []error{io.EOF, errors.New("connection reset")} {
		stats := &CallStats{}
		drainRTP(&errTrack{err: err}, stats)
		if stats.RTPPacketsReceived.Load() != 0 {
			t.Errorf("%v: counted a packet that never arrived", err)
		}
	}
}

type nilThenEnd struct{ served bool }

func (n *nilThenEnd) ReadRTP() (*rtp.Packet, interceptor.Attributes, error) {
	if !n.served {
		n.served = true
		return nil, nil, nil // a nil packet with no error
	}
	return nil, nil, io.EOF
}

func TestANilPacketIsSkippedRatherThanCounted(t *testing.T) {
	stats := &CallStats{}
	drainRTP(&nilThenEnd{}, stats)
	if got := stats.RTPPacketsReceived.Load(); got != 0 {
		t.Errorf("packets = %d, want 0: a nil packet is not a packet", got)
	}
}

func TestTheFirstPacketTimeIsRecordedOnceAndOnlyByTheFirst(t *testing.T) {
	stats := &CallStats{}
	if !stats.FirstPacketAt().IsZero() {
		t.Fatal("first packet time set before any packet")
	}
	drainRTP(&fakeTrack{remaining: 5, payload: []byte{1}}, stats)
	first := stats.FirstPacketAt()
	if first.IsZero() {
		t.Fatal("first packet time not recorded")
	}
	// A second drain on the same stats must not move it: the field answers
	// "when did audio first arrive", not "when did it last".
	time.Sleep(2 * time.Millisecond)
	drainRTP(&fakeTrack{remaining: 5, payload: []byte{1}}, stats)
	if !stats.FirstPacketAt().Equal(first) {
		t.Error("first packet time moved on a later packet")
	}
}

func TestJoinAndFirstPacketAreSeparateFacts(t *testing.T) {
	// A call that joined but never received audio has a join time and no first
	// packet. Collapsing them would hide one-way media, which is exactly the
	// failure the packet count exists to catch.
	stats := &CallStats{}
	stats.markJoined(time.Now())
	if stats.JoinedAt().IsZero() {
		t.Fatal("join time not recorded")
	}
	if !stats.FirstPacketAt().IsZero() {
		t.Fatal("first packet time set by a join with no audio")
	}
}

func TestConcurrentDrainsAccumulateWithoutRacing(t *testing.T) {
	// One goroutine per subscribed track, all writing the same CallStats.
	stats := &CallStats{}
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			drainRTP(&fakeTrack{remaining: 100, payload: make([]byte, 10)}, stats)
		}()
	}
	wg.Wait()
	if got := stats.RTPPacketsReceived.Load(); got != 1600 {
		t.Errorf("packets = %d, want 1600", got)
	}
	if got := stats.RTPBytesReceived.Load(); got != 16000 {
		t.Errorf("bytes = %d, want 16000", got)
	}
}

// --- worker status reporting ------------------------------------------------

func statusUpdates(msgs []*livekit.WorkerMessage) []*livekit.UpdateWorkerStatus {
	var out []*livekit.UpdateWorkerStatus
	for _, m := range msgs {
		if u := m.GetUpdateWorker(); u != nil {
			out = append(out, u)
		}
	}
	return out
}

func TestWorkerStatusIsReportedOnATimer(t *testing.T) {
	// WITHOUT THIS THE RUN STOPS. The server tracks each worker's computed
	// capacity and stops assigning to one that has gone quiet, so a worker that
	// registers and never updates silently receives nothing. The symptom is a
	// ramp that plateaus far below target with no error anywhere.
	f := newFakeServer(t, nil)
	w := newWorker(f)
	w.StatusInterval = 20 * time.Millisecond
	runWorker(t, w)

	waitFor(t, func() bool { return len(statusUpdates(f.messages())) >= 3 })
	for _, u := range statusUpdates(f.messages()) {
		if u.GetStatus() != livekit.WorkerStatus_WS_AVAILABLE {
			t.Errorf("status = %v, want WS_AVAILABLE while idle", u.GetStatus())
		}
	}
}

func TestJobCountRisesAndFallsAroundAJob(t *testing.T) {
	f := newFakeServer(t, sendJob("loadtest"))
	w := newWorker(f)
	w.StatusInterval = 15 * time.Millisecond
	started := make(chan struct{})
	release := make(chan struct{})
	w.OnAssignment = func(_ context.Context, _ Assignment) error {
		close(started)
		<-release
		return nil
	}
	runWorker(t, w)

	<-started
	if got := w.ActiveJobs(); got != 1 {
		t.Fatalf("active jobs = %d while a job is running, want 1", got)
	}
	waitFor(t, func() bool {
		for _, u := range statusUpdates(f.messages()) {
			if u.GetJobCount() == 1 {
				return true
			}
		}
		return false
	})
	close(release)
	waitFor(t, func() bool { return w.ActiveJobs() == 0 })
}

func TestTheReportedLoadIsDerivedNotInvented(t *testing.T) {
	// With no declared capacity there is nothing to divide by, and this reports
	// 0 rather than guessing: the server schedules off this number, so a made-up
	// figure changes where real calls land.
	w := &Worker{}
	if got := w.reportedLoad(); got != 0 {
		t.Errorf("undeclared capacity gave load %v, want 0", got)
	}
	w.MaxJobs = 4
	w.activeJobs.Store(1)
	if got := w.reportedLoad(); got != 0.25 {
		t.Errorf("load = %v, want 0.25", got)
	}
	w.activeJobs.Store(4)
	if got := w.reportedLoad(); got != 1 {
		t.Errorf("load = %v, want 1", got)
	}
	// Never above 1, whatever the count does.
	w.activeJobs.Store(99)
	if got := w.reportedLoad(); got != 1 {
		t.Errorf("load = %v, want it clamped to 1", got)
	}
}

func TestAWorkerAtItsDeclaredCapacityReportsFull(t *testing.T) {
	f := newFakeServer(t, nil)
	w := newWorker(f)
	w.MaxJobs = 1
	w.StatusInterval = 15 * time.Millisecond
	runWorker(t, w)
	w.activeJobs.Store(1)

	waitFor(t, func() bool {
		for _, u := range statusUpdates(f.messages()) {
			if u.GetStatus() == livekit.WorkerStatus_WS_FULL {
				return true
			}
		}
		return false
	})
}

func TestStatusReportingStopsWhenTheContextEnds(t *testing.T) {
	f := newFakeServer(t, nil)
	w := newWorker(f)
	w.StatusInterval = 10 * time.Millisecond
	cancel := runWorker(t, w)
	waitFor(t, func() bool { return len(statusUpdates(f.messages())) >= 2 })
	cancel()
	time.Sleep(60 * time.Millisecond)
	before := len(statusUpdates(f.messages()))
	time.Sleep(80 * time.Millisecond)
	if after := len(statusUpdates(f.messages())); after > before {
		t.Errorf("status kept being reported after cancel: %d -> %d", before, after)
	}
}
