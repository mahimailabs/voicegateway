package scrape

import (
	"context"
	"fmt"
	"log/slog"
	"net/url"
	"strings"
	"time"

	"github.com/livekit/protocol/livekit"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// IngressScraper reads the ingress endpoints of every project on a timer and stores their redacted
// projection.
type IngressScraper struct {
	Findings Findings
	Changed  func(context.Context, string, string)
	Projects Projects
	Samples  IngressSamples
	Lock     Locker
	Dial     Dial
	Log      *slog.Logger
	Now      func() time.Time
	// Every is how often ingress is listed.
	Every time.Duration
	// Timeout bounds each LiveKit call.
	Timeout time.Duration
}

// Run reads every project once per Every until ctx is cancelled. It returns nil on cancellation.
func (s *IngressScraper) Run(ctx context.Context) error {
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

// Tick reads every project's ingress once.
func (s *IngressScraper) Tick(ctx context.Context) {
	projects, err := s.Projects.Projects(ctx)
	if err != nil {
		if ctx.Err() == nil {
			s.Log.Error("scrape: list projects", "err", err)
		}
		return
	}
	eachProject(projects, func(p store.Project) {
		if err := s.Ingress(ctx, p); err != nil && ctx.Err() == nil {
			s.Log.Error("scrape: ingress", "organisation", p.OrganisationID, "project", p.ID, "err", err)
		}
	})
}

// Ingress lists one project's ingress endpoints and stores them with the read. It skips the project
// when another worker holds its lock for this interval. A LiveKit failure is stored as a failed read
// with its class; an error is returned only when nothing could be stored.
func (s *IngressScraper) Ingress(ctx context.Context, p store.Project) error {
	hold := s.Every * 9 / 10
	got, err := s.Lock.Lock(ctx, lockKey(p, store.LayerIngress), hold)
	if err != nil {
		return fmt.Errorf("lock: %w", err)
	}
	if !got {
		return nil
	}
	secret, err := s.Projects.LiveKitSecret(ctx, p.OrganisationID, p.ID)
	if err != nil {
		return fmt.Errorf("open LiveKit secret: %w", err)
	}
	lk := s.Dial(p.LiveKitURL, p.LiveKitAPIKey, secret)

	rctx, cancel := context.WithTimeout(ctx, hold)
	items, err := call(rctx, s.Timeout, lk.ListIngress)
	cancel()
	read := store.LayerRead{OrganisationID: p.OrganisationID, ProjectID: p.ID, Layer: store.LayerIngress, Every: s.Every, ReadAt: s.Now().UTC()}
	var samples []store.IngressSample
	if err != nil {
		if ctx.Err() != nil {
			return nil
		}
		read.ErrorClass = classify(err)
		s.Log.Warn("scrape: ingress read failed", "organisation", p.OrganisationID, "project", p.ID, "class", read.ErrorClass, failure(err))
	} else {
		read.OK = true
		for _, in := range items {
			samples = append(samples, store.IngressSample{
				OrganisationID: p.OrganisationID, ProjectID: p.ID, ReadAt: read.ReadAt, UpdatedAt: nanos(in.GetState().GetUpdatedAt()), Ingress: projectIngress(in),
			})
		}
	}
	sctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	if err := s.Samples.StoreIngressRead(sctx, read, samples); err != nil {
		return fmt.Errorf("store ingress read: %w", err)
	}
	if s.Findings != nil {
		if err := s.Findings.ApplyStandingFindings(sctx, read, ingressCoverage(read.OK, items)); err != nil {
			return fmt.Errorf("store ingress coverage: %w", err)
		}
	}
	if s.Changed != nil {
		s.Changed(ctx, p.OrganisationID, p.ID)
	}
	return nil
}

// projectIngress keeps only what the console contract shows of an ingress endpoint. The stream key,
// the URL's credentials and query values, and the participant's name and metadata are not kept.
// IngressInfo has no creation time, so none is stored.
func projectIngress(in *livekit.IngressInfo) store.Ingress {
	secrets := append([]string{in.GetStreamKey()}, urlParts(in.GetUrl())...)
	st := in.GetState()
	out := store.Ingress{
		ID:          in.GetIngressId(),
		Name:        maskPhoneNumbers(scrub(in.GetName(), secrets)),
		InputType:   in.GetInputType().String(),
		URL:         ingressURL(in.GetUrl()),
		Room:        maskPhoneNumbers(in.GetRoomName()),
		Participant: maskPhoneNumbers(in.GetParticipantIdentity()),
		Reusable:    ptr(in.GetReusable()),
		Status:      st.GetStatus().String(),
		Error:       nonEmpty(maskPhoneNumbers(scrub(st.GetError(), secrets))),
		StartedAt:   nanos(st.GetStartedAt()),
		EndedAt:     nanos(st.GetEndedAt()),
		Published:   []store.IngressTrack{},
	}
	switch {
	case in.EnableTranscoding != nil:
		out.Transcoding = ptr(in.GetEnableTranscoding())
	case in.GetBypassTranscoding(): //nolint:staticcheck // older servers set only the deprecated field
		out.Transcoding = ptr(false)
	}
	if in.Enabled != nil {
		out.Enabled = ptr(in.GetEnabled())
	}
	if a := st.GetAudio(); a != nil {
		out.AudioIn = &store.IngressAudio{
			MimeType:       a.GetMimeType(),
			AverageBitrate: positive(int64(a.GetAverageBitrate())),
			Channels:       positive(int64(a.GetChannels())),
			SampleRate:     positive(int64(a.GetSampleRate())),
		}
	}
	if v := st.GetVideo(); v != nil {
		out.VideoIn = &store.IngressVideo{
			MimeType:  nonEmpty(v.GetMimeType()),
			Width:     positive(int64(v.GetWidth())),
			Height:    positive(int64(v.GetHeight())),
			Framerate: positiveFloat(v.GetFramerate()),
		}
		if b := v.GetAverageBitrate(); b > 0 {
			out.VideoIn.AverageBitrate = ptr(float64(b))
		}
	}
	for _, t := range st.GetTracks() {
		out.Published = append(out.Published, store.IngressTrack{
			Type: t.GetType().String(), MimeType: t.GetMimeType(), Source: t.GetSource().String(),
		})
	}
	return out
}

// ingressURL reduces an ingress URL to its scheme and host: its path can carry a signed token or the
// stream key as well as its query, and its user part credentials. An unreadable URL is not kept.
func ingressURL(raw string) string {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme == "" || u.Host == "" {
		return ""
	}
	return strings.ToLower(u.Scheme) + "://" + u.Host
}

// positive reads a count or rate where LiveKit's 0 means unreported.
func positive(n int64) *int64 {
	if n <= 0 {
		return nil
	}
	return &n
}

func positiveFloat(f float64) *float64 {
	if f <= 0 {
		return nil
	}
	return &f
}
