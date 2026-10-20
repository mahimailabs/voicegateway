package scrape

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/livekit/protocol/livekit"
	"golang.org/x/sync/errgroup"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type JobObservations interface {
	StoreJobObservations(context.Context, store.LayerRead, []store.RoomSample) error
}

// Scraper reads the media server of every project on a timer and stores what it saw.
type Scraper struct {
	Jobs JobObservations
	// Changed runs only after the read is durably stored, including failed source reads.
	Changed  func(context.Context, string, string)
	Projects Projects
	Samples  Samples
	Lock     Locker
	Dial     Dial
	Log      *slog.Logger
	Now      func() time.Time
	// ID names this worker to the others, so it knows when another one read a project last.
	ID string
	// Every is how often rooms are listed.
	Every time.Duration
	// FullEvery is the longest a live room goes without its participants and dispatches being
	// read; a room that is new, changed its participant or publisher count, or was named by a
	// webhook since the last read is read in full at once.
	FullEvery time.Duration
	// Timeout bounds each LiveKit call.
	Timeout time.Duration

	mu   sync.Mutex
	seen map[projectKey]projectState
}

// fullReads is how many rooms of one project are read in full at the same time.
const fullReads = 8

type projectKey struct{ organisationID, projectID string }

// projectState is what this worker's last read of a project found, so an unchanged room is not
// read in full on every tick. It is used only when this worker also did the project's last read.
type projectState struct {
	readAt time.Time
	rooms  map[string]roomState
}

type roomState struct {
	participants, publishers uint32
	fullAt                   time.Time
	dispatches               []*livekit.AgentDispatch
}

// Run reads every project once per Every until ctx is cancelled. It returns nil on cancellation;
// a failure for one project is recorded and logged, never stops the others.
func (s *Scraper) Run(ctx context.Context) error {
	t := time.NewTicker(s.Every)
	defer t.Stop()
	for {
		s.Tick(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
	}
}

// projectsAtOnce bounds how many projects one tick reads at the same time, so an install with many
// projects does not open every project's secret and LiveKit connection at once.
const projectsAtOnce = 16

// eachProject runs read for every project, at most projectsAtOnce at a time, and waits for all.
func eachProject(projects []store.Project, read func(store.Project)) {
	var g errgroup.Group
	g.SetLimit(projectsAtOnce)
	for _, p := range projects {
		g.Go(func() error {
			read(p)
			return nil
		})
	}
	_ = g.Wait()
}

// Tick reads every project's media server once.
func (s *Scraper) Tick(ctx context.Context) {
	projects, err := s.Projects.Projects(ctx)
	if err != nil {
		if ctx.Err() == nil {
			s.Log.Error("scrape: list projects", "err", err)
		}
		return
	}
	eachProject(projects, func(p store.Project) {
		if err := s.MediaServer(ctx, p); err != nil && ctx.Err() == nil {
			s.Log.Error("scrape: media server", "organisation", p.OrganisationID, "project", p.ID, "err", err)
		}
	})
}

// MediaServer reads one project's rooms, and the participants and dispatches of rooms that need a
// full read, then stores them with the read. It skips the project when another worker holds its
// lock for this interval. A LiveKit failure is stored as a failed read with its class; an error is
// returned only when nothing could be stored.
func (s *Scraper) MediaServer(ctx context.Context, p store.Project) error {
	key := projectKey{p.OrganisationID, p.ID}
	lock := lockKey(p, store.LayerMediaServer)
	hold := s.Every * 9 / 10
	got, err := s.Lock.Lock(ctx, lock, hold)
	if err != nil {
		return fmt.Errorf("lock: %w", err)
	}
	if !got {
		return nil
	}
	last, err := s.Lock.Swap(ctx, lock+":reader", s.ID, 2*s.FullEvery)
	if err != nil {
		return fmt.Errorf("last reader: %w", err)
	}
	if last != s.ID {
		s.forget(key) // another worker read it since; what this one remembers may be older
	}
	read := store.LayerRead{OrganisationID: p.OrganisationID, ProjectID: p.ID, Layer: store.LayerMediaServer, Every: s.Every}
	secret, err := s.Projects.LiveKitSecret(ctx, p.OrganisationID, p.ID)
	if err != nil {
		return fmt.Errorf("open LiveKit secret: %w", err)
	}
	lk := s.Dial(p.LiveKitURL, p.LiveKitAPIKey, secret)

	// The whole read ends before the lock does, so no other worker reads the layer meanwhile.
	rctx, cancel := context.WithTimeout(ctx, hold)
	rooms, parts, err := s.readMediaServer(rctx, key, lk)
	cancel()
	read.ReadAt = s.Now().UTC()
	if err != nil {
		if ctx.Err() != nil {
			return nil
		}
		read.ErrorClass = classify(err)
		s.Log.Warn("scrape: media server read failed", "organisation", p.OrganisationID, "project", p.ID, "class", read.ErrorClass)
		s.forget(key)
		return s.store(ctx, read, nil, nil)
	}
	read.OK = true
	for i := range rooms {
		rooms[i].OrganisationID, rooms[i].ProjectID, rooms[i].ReadAt = p.OrganisationID, p.ID, read.ReadAt
	}
	for i := range parts {
		parts[i].OrganisationID, parts[i].ProjectID, parts[i].ReadAt = p.OrganisationID, p.ID, read.ReadAt
	}
	return s.store(ctx, read, rooms, parts)
}

func (s *Scraper) store(ctx context.Context, read store.LayerRead, rooms []store.RoomSample, parts []store.ParticipantSample) error {
	sctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	if err := s.Samples.StoreRead(sctx, read, rooms, parts); err != nil {
		return fmt.Errorf("store read: %w", err)
	}
	if s.Jobs != nil {
		if err := s.Jobs.StoreJobObservations(sctx, read, rooms); err != nil {
			return fmt.Errorf("store job observations: %w", err)
		}
	}
	if s.Changed != nil {
		s.Changed(ctx, read.OrganisationID, read.ProjectID)
	}
	return nil
}

// fullRead is one room's participants and dispatches.
type fullRead struct {
	participants []*livekit.ParticipantInfo
	dispatches   []*livekit.AgentDispatch
	gone         bool
}

// readMediaServer lists the rooms, then reads in full each room that is new, changed, named by a
// webhook since the last read, or has gone FullEvery without a full read.
func (s *Scraper) readMediaServer(ctx context.Context, key projectKey, lk LiveKit) ([]store.RoomSample, []store.ParticipantSample, error) {
	rooms, err := call(ctx, s.Timeout, lk.ListRooms)
	if err != nil {
		return nil, nil, fmt.Errorf("list rooms: %w", err)
	}
	now := s.Now()
	prev := s.snapshot(key)
	named := map[string]bool{}
	if !prev.readAt.IsZero() {
		sids, err := s.Samples.RoomsWithWebhooks(ctx, key.organisationID, key.projectID, prev.readAt)
		if err != nil {
			return nil, nil, fmt.Errorf("rooms with webhooks: %w", err)
		}
		for _, sid := range sids {
			named[sid] = true
		}
	}

	full := make([]*fullRead, len(rooms))
	g, gctx := errgroup.WithContext(ctx)
	g.SetLimit(fullReads)
	for i, r := range rooms {
		st, known := prev.rooms[r.GetSid()]
		if known && !named[r.GetSid()] && st.participants == r.GetNumParticipants() &&
			st.publishers == r.GetNumPublishers() && now.Sub(st.fullAt) < s.FullEvery {
			continue
		}
		g.Go(func() error {
			fr, err := s.readRoom(gctx, lk, r.GetName())
			full[i] = fr
			return err
		})
	}
	if err := g.Wait(); err != nil {
		return nil, nil, err
	}

	next := projectState{readAt: now, rooms: make(map[string]roomState, len(rooms))}
	var roomOut []store.RoomSample
	var partOut []store.ParticipantSample
	for i, r := range rooms {
		fr := full[i]
		if fr == nil {
			// Dispatches as of the room's last full read; participants are not re-stored, so their
			// last seen time stays the last read that saw them.
			st := prev.rooms[r.GetSid()]
			next.rooms[r.GetSid()] = st
			roomOut = append(roomOut, store.RoomSample{Room: projectRoom(r, st.dispatches)})
			continue
		}
		if fr.gone {
			continue // the room ended after it was listed
		}
		agentNames := map[string]string{}
		for _, d := range fr.dispatches {
			for _, j := range d.GetState().GetJobs() {
				if id := j.GetState().GetParticipantIdentity(); id != "" {
					agentNames[id] = d.GetAgentName()
				}
			}
		}
		next.rooms[r.GetSid()] = roomState{
			participants: r.GetNumParticipants(), publishers: r.GetNumPublishers(), fullAt: now, dispatches: fr.dispatches,
		}
		roomOut = append(roomOut, store.RoomSample{Room: projectRoom(r, fr.dispatches)})
		for _, pi := range fr.participants {
			part, state, sip := projectParticipant(pi, agentNames)
			partOut = append(partOut, store.ParticipantSample{RoomSID: r.GetSid(), Participant: part, State: state, SIPCallStatus: sip})
		}
	}
	s.remember(key, next)
	return roomOut, partOut, nil
}

// readRoom reads a room's participants and dispatches. A room that ended meanwhile is gone, not an
// error.
func (s *Scraper) readRoom(ctx context.Context, lk LiveKit, room string) (*fullRead, error) {
	participants, err := call(ctx, s.Timeout, func(ctx context.Context) ([]*livekit.ParticipantInfo, error) {
		return lk.ListParticipants(ctx, room)
	})
	if isNotFound(err) {
		return &fullRead{gone: true}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("list participants: %w", err)
	}
	dispatches, err := call(ctx, s.Timeout, func(ctx context.Context) ([]*livekit.AgentDispatch, error) {
		return lk.ListDispatch(ctx, room)
	})
	if isNotFound(err) {
		return &fullRead{gone: true}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("list dispatches: %w", err)
	}
	if dispatches == nil {
		dispatches = []*livekit.AgentDispatch{}
	}
	return &fullRead{participants: participants, dispatches: dispatches}, nil
}

func (s *Scraper) snapshot(key projectKey) projectState {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.seen[key]
}

func (s *Scraper) remember(key projectKey, st projectState) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.seen == nil {
		s.seen = map[projectKey]projectState{}
	}
	s.seen[key] = st
}

// forget drops what a project's last read found, so the next good read is a full one.
func (s *Scraper) forget(key projectKey) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.seen, key)
}

// call runs one LiveKit call under its own deadline.
func call[T any](ctx context.Context, timeout time.Duration, f func(context.Context) (T, error)) (T, error) {
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	v, err := f(cctx)
	if err != nil && errors.Is(cctx.Err(), context.DeadlineExceeded) && ctx.Err() == nil {
		return v, fmt.Errorf("%w: %w", context.DeadlineExceeded, err)
	}
	return v, err
}

func lockKey(p store.Project, layer string) string {
	return "voicegateway:scrape:" + p.OrganisationID + "/" + p.ID + ":" + layer
}
