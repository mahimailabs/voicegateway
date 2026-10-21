package scrape

import (
	"context"
	"fmt"
	"log/slog"
	"net/url"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/livekit/protocol/livekit"
	lklogger "github.com/livekit/protocol/livekit/logger"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// EgressScraper reads the egress of every project on a timer and stores their redacted projection.
type EgressScraper struct {
	Findings Findings
	Changed  func(context.Context, string, string)
	Projects Projects
	Samples  EgressSamples
	Lock     Locker
	Dial     Dial
	Log      *slog.Logger
	Now      func() time.Time
	// Every is how often egress is listed.
	Every time.Duration
	// Timeout bounds each LiveKit call.
	Timeout time.Duration
}

// Run reads every project once per Every until ctx is cancelled. It returns nil on cancellation.
func (s *EgressScraper) Run(ctx context.Context) error {
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

// Tick reads every project's egress once.
func (s *EgressScraper) Tick(ctx context.Context) {
	projects, err := s.Projects.Projects(ctx)
	if err != nil {
		if ctx.Err() == nil {
			s.Log.Error("scrape: list projects", "err", err)
		}
		return
	}
	eachProject(projects, func(p store.Project) {
		if err := s.Egress(ctx, p); err != nil && ctx.Err() == nil {
			s.Log.Error("scrape: egress", "organisation", p.OrganisationID, "project", p.ID, "err", err)
		}
	})
}

// Egress lists one project's egress and stores them with the read. It skips the project when
// another worker holds its lock for this interval. A LiveKit failure is stored as a failed read
// with its class; an error is returned only when nothing could be stored.
func (s *EgressScraper) Egress(ctx context.Context, p store.Project) error {
	hold := s.Every * 9 / 10
	got, err := s.Lock.Lock(ctx, lockKey(p, store.LayerEgress), hold)
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
	items, err := call(rctx, s.Timeout, lk.ListEgress)
	cancel()
	read := store.LayerRead{OrganisationID: p.OrganisationID, ProjectID: p.ID, Layer: store.LayerEgress, Every: s.Every, ReadAt: s.Now().UTC()}
	var samples []store.EgressSample
	if err != nil {
		if ctx.Err() != nil {
			return nil
		}
		read.ErrorClass = classify(err)
		s.Log.Warn("scrape: egress read failed", "organisation", p.OrganisationID, "project", p.ID, "class", read.ErrorClass)
	} else {
		read.OK = true
		for _, e := range items {
			samples = append(samples, store.EgressSample{
				OrganisationID: p.OrganisationID, ProjectID: p.ID, ReadAt: read.ReadAt, UpdatedAt: nanos(e.GetUpdatedAt()), Egress: projectEgress(e),
			})
		}
	}
	sctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	if err := s.Samples.StoreEgressRead(sctx, read, samples); err != nil {
		return fmt.Errorf("store egress read: %w", err)
	}
	if s.Findings != nil {
		if err := s.Findings.ApplyStandingFindings(sctx, read, egressCoverage(read.OK, items)); err != nil {
			return fmt.Errorf("store egress coverage: %w", err)
		}
	}
	if s.Changed != nil {
		s.Changed(ctx, p.OrganisationID, p.ID)
	}
	return nil
}

// projectEgress keeps only what the console contract shows of an egress. The request is kept as its
// shape; stream outputs as their scheme and host; and any request secret or URL that LiveKit repeats
// in an error or detail text is cut from it.
func projectEgress(e *livekit.EgressInfo) store.Egress {
	secrets := requestSecrets(e)
	out := store.Egress{
		ID:        e.GetEgressId(),
		Room:      maskPhoneNumbers(e.GetRoomName()),
		Status:    e.GetStatus().String(),
		StartedAt: nanos(e.GetStartedAt()),
		EndedAt:   nanos(e.GetEndedAt()),
		Error:     nonEmpty(maskPhoneNumbers(scrub(e.GetError(), secrets))),
		Details:   maskPhoneNumbers(scrub(e.GetDetails(), secrets)),
		Request:   projectEgressRequest(e),
		Streams:   []store.EgressStream{},
	}
	streams := e.GetStreamResults()
	if len(streams) == 0 {
		streams = e.GetStream().GetInfo() //nolint:staticcheck // older servers set only the deprecated result
	}
	for _, si := range streams {
		out.Streams = append(out.Streams, store.EgressStream{
			Host:      streamHost(si.GetUrl()),
			Status:    si.GetStatus().String(),
			Error:     nonEmpty(maskPhoneNumbers(scrub(si.GetError(), secrets))),
			StartedAt: nanos(si.GetStartedAt()),
			EndedAt:   nanos(si.GetEndedAt()),
		})
	}
	files := e.GetFileResults()
	if f := e.GetFile(); len(files) == 0 && f != nil { //nolint:staticcheck // as for streams
		files = []*livekit.FileInfo{f}
	}
	if len(files) > 0 {
		out.File = projectFile(files[0], egressEnded(e.GetStatus()), secrets)
	}
	return out
}

// egressEnded reports whether an egress is in a final status, after which LiveKit has written its
// results.
func egressEnded(s livekit.EgressStatus) bool {
	switch s {
	case livekit.EgressStatus_EGRESS_COMPLETE, livekit.EgressStatus_EGRESS_FAILED,
		livekit.EgressStatus_EGRESS_ABORTED, livekit.EgressStatus_EGRESS_LIMIT_REACHED:
		return true
	}
	return false
}

// projectFile reads a file result. Its size is known only once the egress has ended; while it runs,
// and after a failure that wrote nothing, LiveKit reports 0, which is not a size. The duration is
// nanoseconds; 0 is unreported.
func projectFile(f *livekit.FileInfo, ended bool, secrets []string) *store.EgressFile {
	out := &store.EgressFile{Name: maskPhoneNumbers(scrub(f.GetFilename(), secrets))}
	if ended {
		out.SizeBytes = positive(f.GetSize())
	}
	if d := f.GetDuration(); d > 0 {
		out.DurationS = ptr(float64(d) / 1e9)
	}
	if u, err := url.Parse(f.GetLocation()); err == nil && u.Hostname() != "" {
		out.BucketHost = ptr(u.Hostname())
	}
	return out
}

// upload is any LiveKit message that names one storage provider.
type upload interface {
	GetS3() *livekit.S3Upload
	GetGcp() *livekit.GCPUpload
	GetAzure() *livekit.AzureBlobUpload
	GetAliOSS() *livekit.AliOSSUpload
}

// storageName names the provider an upload writes to, as the console contract does; empty when it
// names none (the server's default storage).
func storageName(u upload) string {
	switch {
	case u == nil:
		return ""
	case u.GetS3() != nil:
		return "s3"
	case u.GetGcp() != nil:
		return "gcp"
	case u.GetAzure() != nil:
		return "azure"
	case u.GetAliOSS() != nil:
		return "aliOSS"
	}
	return ""
}

// Output kinds, in the order the contract lists them.
const (
	outFile     = "fileOutputs"
	outStream   = "streamOutputs"
	outSegments = "segmentOutputs"
	outImages   = "imageOutputs"
	outSocket   = "websocketUrl"
)

// requestShape gathers what a request records and where, without any of its values.
type requestShape struct {
	out     store.EgressRequest
	outputs map[string]bool
}

func (r *requestShape) file(fileType livekit.EncodedFileType, u upload) {
	r.output(outFile, u)
	if r.out.FileType == "" {
		r.out.FileType = fileType.String()
	}
}

func (r *requestShape) output(kind string, u upload) {
	r.outputs[kind] = true
	if r.out.Storage == "" {
		r.out.Storage = storageName(u)
	}
}

// v1Outputs are the output lists of the older per-kind requests.
type v1Outputs interface {
	GetFileOutputs() []*livekit.EncodedFileOutput
	GetStreamOutputs() []*livekit.StreamOutput
	GetSegmentOutputs() []*livekit.SegmentedFileOutput
	GetImageOutputs() []*livekit.ImageOutput
}

func (r *requestShape) v1(req v1Outputs) {
	for _, f := range req.GetFileOutputs() {
		r.file(f.GetFileType(), f)
	}
	if len(req.GetStreamOutputs()) > 0 {
		r.output(outStream, nil)
	}
	for _, s := range req.GetSegmentOutputs() {
		r.output(outSegments, s)
	}
	for _, i := range req.GetImageOutputs() {
		r.output(outImages, i)
	}
}

// v1Single is the one deprecated output of an older request.
type v1Single interface {
	GetFile() *livekit.EncodedFileOutput
	GetStream() *livekit.StreamOutput
	GetSegments() *livekit.SegmentedFileOutput
}

func (r *requestShape) v1Single(req v1Single) {
	if f := req.GetFile(); f != nil {
		r.file(f.GetFileType(), f)
	}
	if req.GetStream() != nil {
		r.output(outStream, nil)
	}
	if s := req.GetSegments(); s != nil {
		r.output(outSegments, s)
	}
}

// v2Request is StartEgressRequest and ExportReplayRequest: a source, outputs and storage.
type v2Request interface {
	GetTemplate() *livekit.TemplateSource
	GetWeb() *livekit.WebSource
	GetMedia() *livekit.MediaSource
	GetOutputs() []*livekit.Output
	GetStorage() *livekit.StorageConfig
}

func (r *requestShape) v2(req v2Request) {
	switch {
	case req.GetTemplate() != nil:
		r.out.Kind = "template"
		r.out.AudioOnly = ptr(req.GetTemplate().GetAudioOnly())
	case req.GetWeb() != nil:
		r.out.Kind = "web"
		r.out.AudioOnly = ptr(req.GetWeb().GetAudioOnly())
	case req.GetMedia() != nil:
		r.out.Kind = "media"
	}
	for _, o := range req.GetOutputs() {
		var u upload
		if o.GetStorage() != nil {
			u = o.GetStorage()
		}
		switch {
		case o.GetFile() != nil:
			r.outputs[outFile] = true
			if r.out.FileType == "" {
				r.out.FileType = o.GetFile().GetFileType().String()
			}
		case o.GetStream() != nil:
			r.outputs[outStream] = true
			u = nil // a stream writes to no storage
		case o.GetSegments() != nil:
			r.outputs[outSegments] = true
		case o.GetImages() != nil:
			r.outputs[outImages] = true
		}
		if r.out.Storage == "" && u != nil {
			r.out.Storage = storageName(u)
		}
	}
	if r.out.Storage == "" && req.GetStorage() != nil {
		r.out.Storage = storageName(req.GetStorage())
	}
}

// projectEgressRequest keeps the shape of the request that started an egress: its kind, whether it
// records audio only, its file type, its storage provider and its output kinds. Credentials, bucket
// names, paths, stream URLs and webhook configs are not kept.
func projectEgressRequest(e *livekit.EgressInfo) store.EgressRequest {
	r := &requestShape{outputs: map[string]bool{}}
	switch req := e.GetRequest().(type) {
	case *livekit.EgressInfo_RoomComposite:
		r.out.Kind = "roomComposite"
		r.out.AudioOnly = ptr(req.RoomComposite.GetAudioOnly())
		r.v1(req.RoomComposite)
		r.v1Single(req.RoomComposite)
	case *livekit.EgressInfo_Web:
		r.out.Kind = "web"
		r.out.AudioOnly = ptr(req.Web.GetAudioOnly())
		r.v1(req.Web)
		r.v1Single(req.Web)
	case *livekit.EgressInfo_Participant:
		r.out.Kind = "participant"
		r.v1(req.Participant)
	case *livekit.EgressInfo_TrackComposite:
		r.out.Kind = "trackComposite"
		r.v1(req.TrackComposite)
		r.v1Single(req.TrackComposite)
	case *livekit.EgressInfo_Track:
		r.out.Kind = "track"
		if f := req.Track.GetFile(); f != nil {
			r.output(outFile, f)
		}
		if req.Track.GetWebsocketUrl() != "" {
			r.outputs[outSocket] = true
		}
	case *livekit.EgressInfo_Egress:
		r.v2(req.Egress)
	case *livekit.EgressInfo_Replay:
		r.v2(req.Replay)
		r.out.Kind = "replay"
	}
	r.out.Outputs = []string{}
	for _, kind := range []string{outFile, outStream, outSegments, outImages, outSocket} {
		if r.outputs[kind] {
			r.out.Outputs = append(r.out.Outputs, kind)
		}
	}
	return r.out
}

// streamHost reduces a stream URL to its scheme and host. A key or credential anywhere else in it
// (path, user, query) is dropped. Shorthand URLs such as mux://<key> carry the key as their host,
// so only their scheme is kept. Nil when the URL cannot be read.
func streamHost(raw string) *string {
	u, err := url.Parse(strings.TrimSpace(strings.TrimSuffix(raw, " live=1")))
	if err != nil || u.Scheme == "" {
		return nil
	}
	scheme := strings.ToLower(u.Scheme)
	if scheme == "mux" || scheme == "twitch" {
		return ptr(scheme + "://")
	}
	if u.Host == "" {
		return nil
	}
	return ptr(scheme + "://" + u.Host)
}

// anyURL matches a URL inside free text, such as an error naming the stream it failed to reach.
var anyURL = regexp.MustCompile(`[A-Za-z][A-Za-z0-9+.-]*://[^\s"'<>,;]+`)

// redacted replaces a request value LiveKit repeats in text.
const redacted = "[redacted]"

// scrub cuts every URL in text down to its scheme and host, and every request secret out of it.
func scrub(text string, secrets []string) string {
	if text == "" {
		return ""
	}
	text = anyURL.ReplaceAllStringFunc(text, func(u string) string {
		if h := streamHost(u); h != nil {
			return *h
		}
		return redacted
	})
	// Longest first, so a value inside another is cut with it.
	secrets = slices.Clone(secrets)
	slices.SortFunc(secrets, func(a, b string) int { return len(b) - len(a) })
	for _, s := range secrets {
		if len(s) >= minSecret {
			text = strings.ReplaceAll(text, s, redacted)
		}
	}
	return text
}

// minSecret is the shortest request value cut from text; shorter ones would cut ordinary words.
const minSecret = 4

// requestSecrets collects the values of an egress request that must never be stored: every field
// LiveKit marks secret or personal (logger.sensitivity), plus the URLs, keys and passwords it leaves
// unmarked (stream and webhook URLs, webhook signing keys, proxy credentials).
func requestSecrets(e *livekit.EgressInfo) []string {
	var out []string
	var walk func(m protoreflect.Message)
	walk = func(m protoreflect.Message) {
		m.Range(func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
			// A map field reports the kind of its values, so maps are told apart first.
			switch {
			case fd.IsMap():
				values := fd.MapValue()
				v.Map().Range(func(_ protoreflect.MapKey, mv protoreflect.Value) bool {
					switch {
					case values.Kind() == protoreflect.MessageKind:
						walk(mv.Message())
					case values.Kind() == protoreflect.StringKind && secretField(fd):
						out = append(out, urlParts(mv.String())...)
					}
					return true
				})
			case fd.Kind() == protoreflect.MessageKind && fd.IsList():
				for i := range v.List().Len() {
					walk(v.List().Get(i).Message())
				}
			case fd.Kind() == protoreflect.MessageKind:
				walk(v.Message())
			case fd.Kind() == protoreflect.StringKind && secretField(fd):
				if fd.IsList() {
					for i := range v.List().Len() {
						out = append(out, urlParts(v.List().Get(i).String())...)
					}
				} else {
					out = append(out, urlParts(v.String())...)
				}
			}
			return true
		})
	}
	switch req := e.GetRequest().(type) {
	case *livekit.EgressInfo_RoomComposite:
		walk(req.RoomComposite.ProtoReflect())
	case *livekit.EgressInfo_Web:
		walk(req.Web.ProtoReflect())
	case *livekit.EgressInfo_Participant:
		walk(req.Participant.ProtoReflect())
	case *livekit.EgressInfo_TrackComposite:
		walk(req.TrackComposite.ProtoReflect())
	case *livekit.EgressInfo_Track:
		walk(req.Track.ProtoReflect())
	case *livekit.EgressInfo_Egress:
		walk(req.Egress.ProtoReflect())
	case *livekit.EgressInfo_Replay:
		walk(req.Replay.ProtoReflect())
	}
	for _, s := range e.GetStreamResults() {
		out = append(out, urlParts(s.GetUrl())...)
	}
	for _, s := range e.GetStream().GetInfo() { //nolint:staticcheck // older servers set only the deprecated result
		out = append(out, urlParts(s.GetUrl())...)
	}
	return slices.DeleteFunc(out, func(s string) bool { return len(s) < minSecret })
}

// urlParts is a secret value and, when it is a URL, each part of it that can carry a key on its own:
// the user and password, every path segment, every query value, and the host of a shorthand URL
// such as mux://<key>. A part LiveKit repeats in an error without the rest of the URL is cut too.
func urlParts(v string) []string {
	out := []string{v}
	u, err := url.Parse(strings.TrimSpace(strings.TrimSuffix(v, " live=1")))
	if err != nil || u.Scheme == "" {
		return out
	}
	if s := strings.ToLower(u.Scheme); s == "mux" || s == "twitch" {
		out = append(out, u.Host)
	}
	if u.User != nil {
		out = append(out, u.User.Username())
		if p, ok := u.User.Password(); ok {
			out = append(out, p)
		}
	}
	out = append(out, strings.Split(u.Path, "/")...)
	for _, vs := range u.Query() {
		out = append(out, vs...)
	}
	return out
}

// unmarkedSecrets are request fields that carry a URL, key or credential without LiveKit's
// sensitivity mark.
var unmarkedSecrets = map[protoreflect.Name]bool{
	"url": true, "urls": true, "websocket_url": true, "signing_key": true,
	"username": true, "password": true, "custom_base_url": true,
}

func secretField(fd protoreflect.FieldDescriptor) bool {
	if unmarkedSecrets[fd.Name()] {
		return true
	}
	if !proto.HasExtension(fd.Options(), lklogger.E_Sensitivity) {
		return false
	}
	s, ok := proto.GetExtension(fd.Options(), lklogger.E_Sensitivity).(lklogger.Sensitivity)
	return ok && s != lklogger.Sensitivity_SENSITIVITY_UNSPECIFIED
}
