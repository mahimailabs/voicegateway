// Package main implements a minimal LiveKit agent worker.
//
// It exists because a SIP load test needs something on the other end of the
// call. livekit-sip does not send 200 OK until it has subscribed to an audio
// track, so this worker's join-and-publish latency IS the call's answer latency,
// and a worker that stalls terminates calls with cannot-subscribe. It sits ON
// the answer path, not beside it.
//
// That is the whole design constraint: any per-call work here (encoding,
// allocation, DNS, token minting, a second publish round-trip) shows up directly
// as SIP answer latency at high concurrency and degrades the establishment rate
// the run is trying to measure.
//
// This file is the worker handshake only: register, answer availability, accept
// an assignment, and hand the room and join token to a callback. Joining the
// room and publishing audio are deliberately somebody else's job, behind
// OnAssignment, so the two can be tested apart.
package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/proto"
)

// Assignment is one job this worker accepted, reduced to what a joiner needs.
//
// Room comes from the assignment itself and is never inferred, matched against a
// pattern, or filtered. See Worker.handleAvailability.
type Assignment struct {
	JobID    string
	RoomName string
	// URL is the server the job says to join, which is not necessarily the one
	// this worker registered against: a server behind a load balancer can hand
	// out a node-specific URL. Empty means "reuse the registration URL".
	URL   string
	Token string
}

// Worker is a registered LiveKit agent worker.
//
// One connection, one goroutine reading it. Nothing here is per-call except the
// availability reply and the callback, because everything else would land on the
// answer path.
type Worker struct {
	// URL is the LiveKit server, ws:// or wss:// (an http/https URL is accepted
	// and rewritten, which is what every LiveKit config file actually contains).
	URL       string
	APIKey    string
	APISecret string
	// AgentName is what the worker registers as. Dispatch matches on it, so it
	// has to equal the agent_name a dispatch rule or a job request names.
	AgentName string
	// PingInterval is advertised at registration. The server pings; this worker
	// answers with a pong.
	PingInterval time.Duration
	// OnAssignment is called once per accepted job, off the read loop, so a slow
	// join cannot stall the connection that other jobs arrive on.
	OnAssignment func(context.Context, Assignment) error
	// Logf is where progress goes. nil means silent, which is what the tests use.
	Logf func(format string, args ...any)

	// MaxJobs is the operator's declared capacity, used only to compute the load
	// this worker reports. Zero means undeclared, and then the reported load is
	// 0: this worker does NOT measure its own load, and reporting a made-up
	// number would put a fabricated reading on the surface the server schedules
	// from.
	MaxJobs int
	// StatusInterval is how often worker status is reported. Zero uses the
	// default.
	StatusInterval time.Duration

	jobsSeen atomic.Int64
	// jobCancels holds one cancel per in-flight job, keyed by job id, so a
	// server-initiated termination can actually stop the job it names.
	jobsMu     sync.Mutex
	jobCancels map[string]context.CancelFunc
	conn       *websocket.Conn
	writeMu    sync.Mutex
	workerID   string
	activeJobs atomic.Int64
}

const (
	// The worker protocol version this speaks. livekit.CurrentWorkerProtocol is
	// the constant the server compares against.
	workerProtocol = livekit.CurrentWorkerProtocol
	// Advertised at registration. The server uses it to decide when a worker has
	// gone silent.
	defaultPingInterval = 10 * time.Second
	// How often worker status is reported when the caller does not say.
	defaultStatusInterval = 5 * time.Second
)

func (w *Worker) logf(format string, args ...any) {
	if w.Logf != nil {
		w.Logf(format, args...)
	}
}

// token mints the registration JWT.
//
// VideoGrant.Agent is the one grant that matters: without it the server refuses
// the /agent upgrade, and the failure surfaces as a bare websocket 401 rather
// than as anything naming the grant.
//
// Minted ONCE, at connect. Minting per call would put JWT signing on the answer
// path for no reason.
func (w *Worker) token() (string, error) {
	if w.APIKey == "" || w.APISecret == "" {
		return "", errors.New("api key and secret are required to register a worker")
	}
	at := auth.NewAccessToken(w.APIKey, w.APISecret).
		SetIdentity("mock-participant").
		SetValidFor(24 * time.Hour).
		SetVideoGrant(&auth.VideoGrant{Agent: true})
	return at.ToJWT()
}

// agentURL turns a LiveKit server URL into the worker registration endpoint.
func agentURL(raw string) (string, error) {
	if strings.TrimSpace(raw) == "" {
		return "", errors.New("a LiveKit server URL is required")
	}
	u, err := url.Parse(raw)
	if err != nil {
		return "", fmt.Errorf("parse server url: %w", err)
	}
	switch u.Scheme {
	case "http":
		u.Scheme = "ws"
	case "https":
		u.Scheme = "wss"
	case "ws", "wss":
	default:
		return "", fmt.Errorf("unsupported scheme %q, want ws, wss, http or https", u.Scheme)
	}
	u.Path = strings.TrimSuffix(u.Path, "/") + "/agent"
	q := u.Query()
	q.Set("protocol", fmt.Sprintf("%d", workerProtocol))
	u.RawQuery = q.Encode()
	return u.String(), nil
}

// Connect dials the server and registers, returning once the server has
// acknowledged the registration.
func (w *Worker) Connect(ctx context.Context) error {
	endpoint, err := agentURL(w.URL)
	if err != nil {
		return err
	}
	jwt, err := w.token()
	if err != nil {
		return err
	}
	header := http.Header{}
	header.Set("Authorization", "Bearer "+jwt)

	conn, resp, err := websocket.DefaultDialer.DialContext(ctx, endpoint, header)
	if err != nil {
		if resp != nil {
			// A 401 here is almost always the Agent grant, so say so rather than
			// leaving the reader with a bare status code.
			return fmt.Errorf(
				"dial %s: %w (http %d; a 401 here usually means the token lacks VideoGrant.Agent)",
				endpoint, err, resp.StatusCode,
			)
		}
		return fmt.Errorf("dial %s: %w", endpoint, err)
	}
	w.conn = conn

	ping := w.PingInterval
	if ping <= 0 {
		ping = defaultPingInterval
	}
	return w.send(&livekit.WorkerMessage{
		Message: &livekit.WorkerMessage_Register{
			Register: &livekit.RegisterWorkerRequest{
				Type:         livekit.JobType_JT_ROOM,
				AgentName:    w.AgentName,
				Version:      "voicegateway-mock-participant",
				PingInterval: uint32(ping / time.Millisecond),
			},
		},
	})
}

// Close shuts the connection down. Safe on a Worker that never connected.
func (w *Worker) Close() error {
	if w.conn == nil {
		return nil
	}
	return w.conn.Close()
}

func (w *Worker) send(msg *livekit.WorkerMessage) error {
	payload, err := proto.Marshal(msg)
	if err != nil {
		return fmt.Errorf("marshal worker message: %w", err)
	}
	// One writer at a time: gorilla panics on concurrent writes, and the
	// availability reply races the pong.
	w.writeMu.Lock()
	defer w.writeMu.Unlock()
	return w.conn.WriteMessage(websocket.BinaryMessage, payload)
}

// Run reads server messages until the connection closes or ctx is cancelled.
func (w *Worker) Run(ctx context.Context) error {
	if w.conn == nil {
		return errors.New("Run called before Connect")
	}
	go func() {
		<-ctx.Done()
		_ = w.conn.Close()
	}()
	go w.reportStatusUntil(ctx)
	for {
		_, payload, err := w.conn.ReadMessage()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("read: %w", err)
		}
		var msg livekit.ServerMessage
		if err := proto.Unmarshal(payload, &msg); err != nil {
			// A frame this build cannot parse is skipped rather than fatal: a
			// newer server adding a message type must not take the worker down
			// mid-run.
			w.logf("skipping unparseable server message: %v", err)
			continue
		}
		if err := w.handle(ctx, &msg); err != nil {
			return err
		}
	}
}

func (w *Worker) handle(ctx context.Context, msg *livekit.ServerMessage) error {
	switch m := msg.Message.(type) {
	case *livekit.ServerMessage_Register:
		w.workerID = m.Register.GetWorkerId()
		w.logf("registered as worker %s (agent name %q)", w.workerID, w.AgentName)
	case *livekit.ServerMessage_Availability:
		return w.handleAvailability(m.Availability)
	case *livekit.ServerMessage_Assignment:
		return w.handleAssignment(ctx, m.Assignment)
	case *livekit.ServerMessage_Termination:
		// Logging this is not handling it. Until the job's context is cancelled
		// the job goroutine runs on, its deferred activeJobs decrement never
		// fires, and this worker's reported load climbs monotonically. Once that
		// load crosses the server's dispatch threshold the worker stops being
		// given work while still looking healthy: registered, connected, silent.
		id := m.Termination.GetJobId()
		w.logf("job %s terminated by the server", id)
		w.jobsMu.Lock()
		cancelJob := w.jobCancels[id]
		w.jobsMu.Unlock()
		if cancelJob != nil {
			cancelJob()
		}
	case *livekit.ServerMessage_Pong:
		// Nothing to do. Presence is the point.
	default:
		w.logf("ignoring server message this build does not handle")
	}
	return nil
}

// handleAvailability answers every availability request with available:true.
//
// NO ROOM-NAME FILTERING, deliberately and permanently. It is tempting to accept
// only rooms matching some prefix, and every prefix that has been written down
// for this deployment has been wrong at least once: docs name one, live configs
// use others. A worker that filters silently declines the jobs it was started
// for, and the failure looks like the dispatcher never dispatched. The dispatch
// rule decides which worker gets a job; the worker's job is to answer.
func (w *Worker) handleAvailability(req *livekit.AvailabilityRequest) error {
	job := req.GetJob()
	w.logf("available for job %s in room %q", job.GetId(), job.GetRoom().GetName())
	return w.send(&livekit.WorkerMessage{
		Message: &livekit.WorkerMessage_Availability{
			Availability: &livekit.AvailabilityResponse{
				JobId:               job.GetId(),
				Available:           true,
				SupportsResume:      false,
				ParticipantIdentity: "mock-participant-" + job.GetId(),
			},
		},
	})
}

// handleAssignment reads the room and join token out of the assignment.
//
// Both come FROM the assignment. The room name is not derived from a pattern and
// the token is not minted here: the server issues a job-scoped one, and minting
// a second would both be wrong and put signing on the answer path.
func (w *Worker) handleAssignment(ctx context.Context, a *livekit.JobAssignment) error {
	job := a.GetJob()
	assignment := Assignment{
		JobID:    job.GetId(),
		RoomName: job.GetRoom().GetName(),
		URL:      a.GetUrl(),
		Token:    a.GetToken(),
	}
	if assignment.URL == "" {
		assignment.URL = w.URL
	}
	w.logf("assigned job %s -> room %q", assignment.JobID, assignment.RoomName)
	if w.OnAssignment == nil {
		// Nothing to run, but the server has already registered this job's
		// terminate topic and will hold it until told the job ended. Say so
		// rather than leaving it allocated for a job that never started.
		return w.reportJobEnded(assignment.JobID, nil)
	}
	// Off the read loop: a slow join must not stall the connection the next job
	// arrives on.
	// The job gets its own context so ServerMessage_Termination can end THIS
	// job. Without it the only cancel in the process is the process-wide one,
	// and a terminated job runs until shutdown while still counting against
	// this worker's reported load.
	jobCtx, cancelJob := context.WithCancel(ctx)
	w.jobsMu.Lock()
	if w.jobCancels == nil {
		w.jobCancels = make(map[string]context.CancelFunc)
	}
	w.jobCancels[assignment.JobID] = cancelJob
	w.jobsMu.Unlock()

	w.activeJobs.Add(1)
	w.jobsSeen.Add(1)
	go func() {
		var jobErr error
		defer func() {
			cancelJob()
			w.jobsMu.Lock()
			delete(w.jobCancels, assignment.JobID)
			w.jobsMu.Unlock()
			// Before the local bookkeeping, because this is the message the
			// SERVER is waiting for. See reportJobEnded.
			if err := w.reportJobEnded(assignment.JobID, jobErr); err != nil {
				w.logf("job %s: end status not reported: %v", assignment.JobID, err)
			}
			w.activeJobs.Add(-1)
			// Report immediately on the way out rather than waiting for the next
			// tick, so capacity that has just freed up is visible to the
			// scheduler now.
			_ = w.reportStatus()
		}()
		if jobErr = w.OnAssignment(jobCtx, assignment); jobErr != nil {
			w.logf("job %s failed: %v", assignment.JobID, jobErr)
		}
	}()
	// And immediately on the way in, so a burst of assignments cannot look like
	// an idle worker for a whole tick.
	return w.reportStatus()
}

// ActiveJobs is how many assignments are currently in flight.
func (w *Worker) ActiveJobs() int64 { return w.activeJobs.Load() }

// reportJobEnded tells the server that one job is over.
//
// THIS IS SERVER-SIDE CLEANUP, NOT BOOKKEEPING, and omitting it leaks memory on
// the LiveKit node this worker registered with. On accepting an assignment the
// server calls RegisterJobTerminateTopic for the job (pkg/service/agentservice.go)
// and holds a psrpc handler plus its bus subscriptions open. It releases them in
// exactly two places: when the worker disconnects entirely, and when it receives
// an UpdateJobStatus whose status is SUCCESS or FAILED. A worker that keeps one
// long-lived connection for a whole load test and never sends this hits neither,
// so every job it has ever been given stays allocated until the process exits.
// The same message is what removes the job from the worker's runningJobs map
// server-side (pkg/agent/worker.go), so that grows without bound too.
//
// Measured on the 24 hour soak at 100 concurrent: the SFU holding this worker's
// registration climbed from 37,364 to 123,662 goroutines over 26,000 calls, about
// 3.3 per job, while the second SFU carrying identical call load stayed flat at
// ~19,000. Locally, with all rooms deleted, the same 3.3 per dispatch reproduced
// against livekit-server 1.13.5 and the stacks were psrpc bus subscription reads.
//
// Sent on EVERY exit path including failure, because the server does not care
// which of SUCCESS or FAILED it gets; it cares that the status is a terminal one.
// A job that errored and reports nothing leaks exactly as much as one that hung.
func (w *Worker) reportJobEnded(jobID string, jobErr error) error {
	status := livekit.JobStatus_JS_SUCCESS
	detail := ""
	if jobErr != nil {
		status = livekit.JobStatus_JS_FAILED
		detail = jobErr.Error()
	}
	return w.send(&livekit.WorkerMessage{
		Message: &livekit.WorkerMessage_UpdateJob{
			UpdateJob: &livekit.UpdateJobStatus{
				JobId:  jobID,
				Status: status,
				Error:  detail,
			},
		},
	})
}

// reportedLoad is the load this worker declares, in 0..1.
//
// Derived from a real job count against an operator-declared MaxJobs, never
// measured. With MaxJobs unset there is nothing to divide by, and this returns
// 0 rather than inventing a figure: the server schedules off this number, so a
// guess here changes where real calls land.
func (w *Worker) reportedLoad() float32 {
	if w.MaxJobs <= 0 {
		return 0
	}
	load := float32(w.activeJobs.Load()) / float32(w.MaxJobs)
	if load > 1 {
		return 1
	}
	return load
}

func (w *Worker) reportStatus() error {
	status := livekit.WorkerStatus_WS_AVAILABLE
	if w.MaxJobs > 0 && w.activeJobs.Load() >= int64(w.MaxJobs) {
		status = livekit.WorkerStatus_WS_FULL
	}
	jobs := w.activeJobs.Load()
	if jobs < 0 {
		jobs = 0
	}
	return w.send(&livekit.WorkerMessage{
		Message: &livekit.WorkerMessage_UpdateWorker{
			UpdateWorker: &livekit.UpdateWorkerStatus{
				Status:   &status,
				Load:     w.reportedLoad(),
				JobCount: uint32(jobs),
			},
		},
	})
}

// reportStatusUntil reports worker status on a timer until ctx ends.
//
// WITHOUT THIS THE RUN STOPS. The server tracks each worker's computed capacity
// and stops assigning jobs to one that has gone quiet, so a worker that
// registers and then never updates drains to zero capacity and silently receives
// nothing. The symptom is a load test that ramps to a plateau far below its
// target with no error anywhere.
func (w *Worker) reportStatusUntil(ctx context.Context) {
	every := w.StatusInterval
	if every <= 0 {
		every = defaultStatusInterval
	}
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := w.reportStatus(); err != nil {
				// Returning silently here assumes the read loop will notice
				// the same failure. It will not always: ReadMessage has no
				// deadline, so a connection dead only in the write direction
				// leaves the read loop blocked while this goroutine exits. The
				// worker then stays registered and receives nothing, because a
				// worker that stops reporting is drained to zero capacity.
				//
				// This was NOT the cause of the load-threshold stall that
				// prompted the change (that was jobs never being cancelled, see
				// ServerMessage_Termination above; this path was instrumented
				// during that investigation and never fired). It is fixed
				// anyway, because the failure mode it allows is the same one:
				// silent, permanent, and invisible to every health signal.
				//
				// Say what happened, then close the connection so the read loop
				// fails and the supervisor restarts us. A visible restart beats
				// a silent zombie.
				w.logf("STATUS REPORT FAILED after %d jobs (active=%d): %v",
					w.jobsSeen.Load(), w.activeJobs.Load(), err)
				_ = w.conn.Close()
				return
			}
		}
	}
}
