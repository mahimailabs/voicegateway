package capacity

import (
	"bytes"
	"context"
	_ "embed"
	"errors"
	"fmt"
	"io"
	"sync"
	"time"

	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/livekit/server-sdk-go/v2/pkg/oggreader"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

// line.ogg is the caller's line: the question in examples/livekit-agent/caller.ogg, cut to the
// speech and encoded as Opus in 20 ms packets.
//
//go:embed line.ogg
var lineOgg []byte

// agentState is the participant attribute LiveKit Agents keeps its state in: initializing,
// listening, thinking or speaking.
const agentState = "lk.agent.state"

// The caller's timing: how long it waits for the agent to finish its greeting, how long an answer
// may take to finish, and the pause before it says its line again.
const (
	greetingWithin = 20 * time.Second
	settled        = 1500 * time.Millisecond
	answerFor      = 30 * time.Second
	pause          = time.Second
	frame          = 20 * time.Millisecond
)

// silence is a 20 ms Opus frame of silence (RFC 6716 TOC 0xF8: CELT-only fullband, 20 ms, one
// frame), sent between lines so the agent's audio stream never stalls.
var silence = []byte{0xF8, 0xFF, 0xFE}

type packet struct {
	data     []byte
	duration time.Duration
}

// line is the caller's line as Opus packets, read once with server-sdk-go's Ogg reader.
var line = sync.OnceValues(func() ([]packet, error) {
	r, _, err := oggreader.NewOggReader(bytes.NewReader(lineOgg))
	if err != nil {
		return nil, fmt.Errorf("read the caller's line: %w", err)
	}
	var out []packet
	for {
		p, err := r.ReadPacket()
		if errors.Is(err, io.EOF) {
			return out, nil
		}
		if err != nil {
			return nil, fmt.Errorf("read the caller's line: %w", err)
		}
		d, err := oggreader.ParsePacketDuration(p)
		if err != nil {
			return nil, fmt.Errorf("read the caller's line: %w", err)
		}
		out = append(out, packet{data: p, duration: d})
	}
})

// caller is one synthetic call's participant. It waits for the agent's greeting to end, says its
// line, times the agent's answer from the agent's state, and says it again after each answer.
type caller struct {
	room   string
	events chan<- Event
	now    func() time.Time
	lk     *lksdk.Room
	track  *lksdk.LocalTrack
	line   []packet

	mu     sync.Mutex
	agent  string
	joined bool
	// closed is set, under mu, before done closes, so no goroutine starts once hangup waits.
	closed bool
	// states carries the agent's state changes, with when each arrived.
	states chan stateChange
	// speak hands the writer a line to send; the writer closes the channel it is given once the
	// line's last packet is out.
	speak chan chan struct{}
	done  chan struct{}
	once  sync.Once
	wg    sync.WaitGroup
}

type stateChange struct {
	state string
	at    time.Time
}

// dialCaller connects a caller to room and starts it. The returned hangup disconnects it and waits
// for its goroutines.
func dialCaller(ctx context.Context, url, apiKey, apiSecret, room string, events chan<- Event, now func() time.Time) (func(), error) {
	pkts, err := line()
	if err != nil {
		return nil, err
	}
	c := &caller{
		room: room, events: events, now: now, line: pkts,
		states: make(chan stateChange, 32), speak: make(chan chan struct{}), done: make(chan struct{}),
	}
	cb := &lksdk.RoomCallback{
		OnParticipantConnected:    c.onConnected,
		OnParticipantDisconnected: c.onDisconnected,
		OnDisconnected:            func() { c.send(Event{Kind: Lost}) },
		ParticipantCallback: lksdk.ParticipantCallback{
			OnAttributesChanged: c.onAttributes,
		},
	}
	connected := make(chan error, 1)
	go func() {
		r, err := lksdk.ConnectToRoom(url, lksdk.ConnectInfo{
			APIKey: apiKey, APISecret: apiSecret, RoomName: room,
			ParticipantIdentity: "vg-capacity-caller", ParticipantName: "VoiceGateway capacity test",
			ParticipantMetadata: testMetadata, ParticipantAttributes: map[string]string{"voicegateway.capacity-test": "true"},
		}, cb, lksdk.WithAutoSubscribe(true))
		c.lk = r
		connected <- err
	}()
	select {
	case err := <-connected:
		if err != nil {
			return nil, fmt.Errorf("join test room: %w", err)
		}
	case <-ctx.Done():
		go func() {
			if <-connected == nil {
				c.lk.Disconnect()
			}
		}()
		return nil, ctx.Err()
	}
	track, err := lksdk.NewLocalTrack(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2})
	if err == nil {
		_, err = c.lk.LocalParticipant.PublishTrack(track, &lksdk.TrackPublicationOptions{Name: "caller", Source: livekit.TrackSource_MICROPHONE})
	}
	if err != nil {
		c.lk.Disconnect()
		return nil, fmt.Errorf("publish the caller's audio: %w", err)
	}
	c.track = track
	// An agent already in the room when the caller arrived.
	for _, p := range c.lk.GetRemoteParticipants() {
		c.onConnected(p)
	}
	c.mu.Lock()
	c.wg.Add(1)
	c.mu.Unlock()
	go c.write()
	return c.hangup, nil
}

func (c *caller) hangup() {
	c.once.Do(func() {
		c.mu.Lock()
		c.closed = true
		c.mu.Unlock()
		close(c.done)
		c.lk.Disconnect()
	})
	c.wg.Wait()
}

// send hands an event to the run, unless the caller is hanging up.
func (c *caller) send(ev Event) {
	select {
	case <-c.done:
		return
	default:
	}
	ev.Room = c.room
	if ev.At.IsZero() {
		ev.At = c.now()
	}
	select {
	case c.events <- ev:
	case <-c.done:
	}
}

func (c *caller) onConnected(p *lksdk.RemoteParticipant) {
	if p.Kind() != lksdk.ParticipantAgent {
		return
	}
	c.mu.Lock()
	if c.joined || c.closed {
		c.mu.Unlock()
		return
	}
	c.joined, c.agent = true, p.Identity()
	c.wg.Add(1)
	c.mu.Unlock()
	c.send(Event{Kind: Joined})
	if s, ok := p.Attributes()[agentState]; ok {
		c.state(s)
	}
	go c.talk()
}

func (c *caller) onDisconnected(p *lksdk.RemoteParticipant) {
	c.mu.Lock()
	agent := c.joined && p.Identity() == c.agent
	c.mu.Unlock()
	if agent {
		c.send(Event{Kind: Left})
	}
}

func (c *caller) onAttributes(changed map[string]string, p lksdk.Participant) {
	s, ok := changed[agentState]
	if !ok {
		return
	}
	c.mu.Lock()
	agent := c.joined && p.Identity() == c.agent
	c.mu.Unlock()
	if agent {
		c.state(s)
	}
}

func (c *caller) state(s string) {
	select {
	case c.states <- stateChange{state: s, at: c.now()}:
	default:
		// The talk loop is behind by 32 changes; it reads the newest state on its next wait.
	}
}

// write sends the caller's audio, one packet every 20 ms: the line when talk asks for it, silence
// otherwise.
func (c *caller) write() {
	defer c.wg.Done()
	t := time.NewTicker(frame)
	defer t.Stop()
	var (
		queue []packet
		ended chan struct{}
	)
	for {
		select {
		case <-c.done:
			return
		case e := <-c.speak:
			queue, ended = c.line, e
			continue
		case <-t.C:
		}
		p := packet{data: silence, duration: frame}
		if len(queue) > 0 {
			p, queue = queue[0], queue[1:]
		}
		_ = c.track.WriteSample(media.Sample{Data: p.data, Duration: p.duration}, nil)
		if ended != nil && len(queue) == 0 {
			close(ended)
			ended = nil
		}
	}
}

// talk waits for the agent's greeting to end, then says the line and times each answer until the
// caller hangs up.
func (c *caller) talk() {
	defer c.wg.Done()
	state := ""
	// The greeting: wait until the agent has been listening for a moment, or give up waiting.
	deadline := time.NewTimer(greetingWithin)
	defer deadline.Stop()
	quiet := time.NewTimer(settled)
	defer quiet.Stop()
greeting:
	for {
		select {
		case <-c.done:
			return
		case ch := <-c.states:
			state = ch.state
			quiet.Reset(settled)
		case <-quiet.C:
			if state == "listening" {
				break greeting
			}
			quiet.Reset(settled)
		case <-deadline.C:
			break greeting
		}
	}
	for {
		// Changes before the line are not an answer to it.
		for len(c.states) > 0 {
			state = (<-c.states).state
		}
		ended := make(chan struct{})
		select {
		case c.speak <- ended:
		case <-c.done:
			return
		}
		select {
		case <-ended:
		case <-c.done:
			return
		}
		said := c.now()
		answered, ok := c.waitFor(func(s string) bool { return s == "speaking" }, said, AnswerWithin, &state)
		if !ok {
			return
		}
		if answered.IsZero() {
			c.send(Event{Kind: NoAnswer})
			return
		}
		c.send(Event{Kind: Answered, At: answered, Seconds: answered.Sub(said).Seconds()})
		if _, ok := c.waitFor(func(s string) bool { return s != "speaking" }, answered, answerFor, &state); !ok {
			return
		}
		select {
		case <-time.After(pause):
		case <-c.done:
			return
		}
	}
}

// waitFor waits up to within for a state change at or after since that satisfies want. It returns
// when the change arrived (zero on timeout) and false when the caller is hanging up.
func (c *caller) waitFor(want func(string) bool, since time.Time, within time.Duration, state *string) (time.Time, bool) {
	t := time.NewTimer(within)
	defer t.Stop()
	for {
		select {
		case <-c.done:
			return time.Time{}, false
		case <-t.C:
			return time.Time{}, true
		case ch := <-c.states:
			*state = ch.state
			if !ch.at.Before(since) && want(ch.state) {
				return ch.at, true
			}
		}
	}
}
