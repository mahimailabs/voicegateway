package scrape

import (
	"context"
	"fmt"
	"log/slog"
	"net/url"
	"strings"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/types/known/timestamppb"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// CloudAgentScraper reads the LiveKit Cloud agents of every project on a timer and stores their
// redacted projection. A project whose server is not LiveKit Cloud is recorded as a good read where
// Cloud agents do not apply, without any Cloud API call.
type CloudAgentScraper struct {
	Projects Projects
	Samples  CloudAgentSamples
	Lock     Locker
	Dial     Dial
	Log      *slog.Logger
	Now      func() time.Time
	// Every is how often the agents are read.
	Every time.Duration
	// Timeout bounds each LiveKit call.
	Timeout time.Duration
}

// Run reads every project once per Every until ctx is cancelled. It returns nil on cancellation.
func (s *CloudAgentScraper) Run(ctx context.Context) error {
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

// Tick reads every project's Cloud agents once.
func (s *CloudAgentScraper) Tick(ctx context.Context) {
	projects, err := s.Projects.Projects(ctx)
	if err != nil {
		if ctx.Err() == nil {
			s.Log.Error("scrape: list projects", "err", err)
		}
		return
	}
	eachProject(projects, func(p store.Project) {
		if err := s.CloudAgents(ctx, p); err != nil && ctx.Err() == nil {
			s.Log.Error("scrape: cloud agents", "organisation", p.OrganisationID, "project", p.ID, "err", err)
		}
	})
}

// CloudAgents reads one project's Cloud agents and their versions and stores them with the read. It
// skips the project when another worker holds its lock for this interval. A LiveKit failure is
// stored as a failed read with its class; an error is returned only when nothing could be stored.
func (s *CloudAgentScraper) CloudAgents(ctx context.Context, p store.Project) error {
	hold := s.Every * 9 / 10
	got, err := s.Lock.Lock(ctx, lockKey(p, store.LayerCloudAgents), hold)
	if err != nil {
		return fmt.Errorf("lock: %w", err)
	}
	if !got {
		return nil
	}
	read := store.LayerRead{OrganisationID: p.OrganisationID, ProjectID: p.ID, Layer: store.LayerCloudAgents, Every: s.Every}
	var agents *store.CloudAgents
	if !IsLiveKitCloud(p.LiveKitURL) {
		// Cloud agents exist only on LiveKit Cloud; the read is good and says they do not apply.
		read.ReadAt, read.OK = s.Now().UTC(), true
		agents = &store.CloudAgents{Agents: []store.CloudAgent{}}
	} else {
		secret, err := s.Projects.LiveKitSecret(ctx, p.OrganisationID, p.ID)
		if err != nil {
			return fmt.Errorf("open LiveKit secret: %w", err)
		}
		lk := s.Dial(p.LiveKitURL, p.LiveKitAPIKey, secret)
		rctx, cancel := context.WithTimeout(ctx, hold)
		agents, err = s.readAgents(rctx, lk)
		cancel()
		read.ReadAt = s.Now().UTC()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			read.ErrorClass = classify(err)
			s.Log.Warn("scrape: cloud agents read failed", "organisation", p.OrganisationID, "project", p.ID, "class", read.ErrorClass)
			agents = nil
		} else {
			read.OK = true
		}
	}
	sctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	if err := s.Samples.StoreCloudAgentsRead(sctx, read, agents); err != nil {
		return fmt.Errorf("store cloud agents read: %w", err)
	}
	return nil
}

func (s *CloudAgentScraper) readAgents(ctx context.Context, lk LiveKit) (*store.CloudAgents, error) {
	infos, err := call(ctx, s.Timeout, lk.ListAgents)
	if err != nil {
		return nil, fmt.Errorf("list agents: %w", err)
	}
	out := &store.CloudAgents{Applies: true, Agents: make([]store.CloudAgent, 0, len(infos))}
	for _, a := range infos {
		versions, err := call(ctx, s.Timeout, func(ctx context.Context) ([]*livekit.AgentVersion, error) {
			return lk.ListAgentVersions(ctx, a.GetAgentId())
		})
		if err != nil {
			return nil, fmt.Errorf("list agent versions: %w", err)
		}
		out.Agents = append(out.Agents, projectCloudAgent(a, versions))
	}
	return out, nil
}

// IsLiveKitCloud reports whether a project's LiveKit URL is a LiveKit Cloud project, whose hosts
// end in .livekit.cloud. server-sdk-go v2.19.0 has no helper for this: its AgentClient derives the
// agents host from any URL.
func IsLiveKitCloud(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return false
	}
	return strings.HasSuffix(strings.ToLower(u.Hostname()), ".livekit.cloud")
}

// projectCloudAgent keeps only what the console contract shows of a Cloud agent. Its secrets (names
// and values), the versions' attributes and owners, and the deployments' events are not kept.
func projectCloudAgent(a *livekit.AgentInfo, versions []*livekit.AgentVersion) store.CloudAgent {
	out := store.CloudAgent{
		ID:          a.GetAgentId(),
		Name:        nonEmpty(a.GetAgentName()),
		Version:     reportedVersion(a.GetVersion()),
		DeployedAt:  timestamp(a.GetDeployedAt()),
		Deployments: make([]store.CloudAgentDeployment, 0, len(a.GetAgentDeployments())),
		Versions:    make([]store.CloudAgentVersion, 0, len(versions)),
	}
	for _, d := range a.GetAgentDeployments() {
		out.Deployments = append(out.Deployments, store.CloudAgentDeployment{
			Deployment:    nonEmpty(d.GetDeployment()),
			Region:        d.GetRegion(),
			ServerRegion:  d.GetServerRegion(),
			Status:        d.GetStatus(),
			Replicas:      positive(int64(d.GetReplicas())),
			MinReplicas:   positive(int64(d.GetMinReplicas())),
			MaxReplicas:   positive(int64(d.GetMaxReplicas())),
			CurCPU:        nonEmpty(d.GetCurCpu()),
			CPUReq:        nonEmpty(d.GetCpuReq()),
			CPULimit:      nonEmpty(d.GetCpuLimit()),
			CurMem:        nonEmpty(d.GetCurMem()),
			MemReq:        nonEmpty(d.GetMemReq()),
			MemLimit:      nonEmpty(d.GetMemLimit()),
			LastScrapedAt: timestamp(d.GetLastScrapedAt()),
			Version:       d.GetVersion(),
		})
	}
	for _, v := range versions {
		out.Versions = append(out.Versions, store.CloudAgentVersion{
			Version: v.GetVersion(),
			// A proto3 bool has no unset state, so each flag is kept as reported: false is what
			// AgentVersion carries for a version that is not current, draining or active.
			Current:    ptr(v.GetCurrent()),
			Draining:   ptr(v.GetDraining()),
			Active:     ptr(v.GetActive()),
			Status:     v.GetStatus(),
			CreatedAt:  timestamp(v.GetCreatedAt()),
			DeployedAt: timestamp(v.GetDeployedAt()),
		})
	}
	return out
}

// reportedVersion is a version LiveKit reported: empty and the "---" placeholder lk shows for an
// agent with no version are unreported.
func reportedVersion(v string) *string {
	if v == "" || v == "---" {
		return nil
	}
	return &v
}

// timestamp reads a protobuf time; absent, the Unix epoch and anything before it (such as Go's zero
// time 0001-01-01) mean unreported.
func timestamp(ts *timestamppb.Timestamp) *time.Time {
	if ts == nil || ts.GetSeconds() <= 0 {
		return nil
	}
	t := ts.AsTime().UTC()
	return &t
}
