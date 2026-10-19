package ingest

import (
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime"
	"net/http"
	"strings"
	"time"

	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/durationpb"

	"github.com/mahimailabs/voicegateway/internal/event"
)

// Limits on what an agent uploads. A batch of spans is tens of KiB; the session upload carries the
// whole recording, which is read and discarded, never held.
const (
	maxOTLPBody        = 4 << 20
	maxOTLPDecoded     = 16 << 20
	maxSessionUpload   = 512 << 20
	maxSessionMetadata = 16 << 20
)

// Uploads receives a LiveKit agent's observability uploads at LiveKit's own paths (decision 0014),
// as LiveKit Agents 1.8.7 sends them (livekit/agents/telemetry/traces.py): a bearer JWT from the
// agent's LiveKit key and secret with the observability write grant, and no body hash.
type Uploads struct {
	Quota    Quota
	Projects Projects
	Stream   Appender
	Log      *slog.Logger
	Now      func() time.Time
}

// OTLP answers one of the three OTLP over HTTP exports with the given kind.
func (u *Uploads) OTLP(kind event.Kind) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		keys, ok := u.verify(w, r)
		if !ok {
			return
		}
		body, err := readOTLP(http.MaxBytesReader(w, r.Body, maxOTLPBody), r.Header.Get("Content-Encoding"))
		if err != nil {
			u.Log.Warn("upload: unreadable OTLP body", "kind", kind, "project", keys.project.ID, "reason", err.Error())
			http.Error(w, "unreadable body", statusFor(err))
			return
		}
		if !u.appendEnvelope(w, r, keys, event.Envelope{Kind: kind, Payload: body}) {
			return
		}
		// An empty Export*ServiceResponse: full success, encoded as zero bytes.
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(http.StatusOK)
	})
}

// SessionReport answers the multipart session upload: the recording header, chat_history.json and
// recording.ogg. The audio is discarded until a project has a recordings bucket (0015).
func (u *Uploads) SessionReport(w http.ResponseWriter, r *http.Request) {
	keys, ok := u.verify(w, r)
	if !ok {
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxSessionUpload)
	report, jobID, err := readSessionReport(r)
	if err != nil {
		u.Log.Warn("upload: unreadable session report", "project", keys.project.ID, "reason", err.Error())
		http.Error(w, "unreadable session report", statusFor(err))
		return
	}
	payload, err := json.Marshal(report)
	if err != nil {
		u.Log.Error("upload: encode session report", "project", keys.project.ID, "err", err)
		http.Error(w, "encode failed", http.StatusInternalServerError)
		return
	}
	// The job id names this report; a retried upload carries the same one. Without one the id is
	// unknown and stays empty.
	if !u.appendEnvelope(w, r, keys, event.Envelope{ID: jobID, Kind: event.KindAgentSessionReport, Payload: payload}) {
		return
	}
	w.WriteHeader(http.StatusOK)
}

// verify checks the bearer JWT against the project that owns its key and requires the
// observability write grant. It answers the request itself and returns false when it may not
// proceed. A 401 body must never contain the phrases with which LiveKit Cloud tells the agent to
// stop uploading for good ("data recording is disabled", "disabled by owner").
func (u *Uploads) verify(w http.ResponseWriter, r *http.Request) (*projectKeys, bool) {
	keys, code := u.check(r)
	switch code {
	case http.StatusOK:
		return keys, checkQuota(w, r, u.Quota, keys.project.OrganisationID)
	case http.StatusServiceUnavailable:
		unavailable(w)
	default:
		http.Error(w, http.StatusText(code), code)
	}
	return nil, false
}

func (u *Uploads) check(r *http.Request) (*projectKeys, int) {
	raw, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !ok || raw == "" {
		u.Log.Warn("upload: not verified", "path", r.URL.Path, "reason", "no bearer token")
		return nil, http.StatusUnauthorized
	}
	v, err := auth.ParseAPIToken(raw)
	if err != nil {
		u.Log.Warn("upload: not verified", "path", r.URL.Path, "reason", err.Error())
		return nil, http.StatusUnauthorized
	}
	keys := &projectKeys{ctx: r.Context(), projects: u.Projects}
	secret := keys.GetSecret(v.APIKey())
	if keys.err != nil {
		u.Log.Error("upload: project lookup failed", "err", keys.err)
		return nil, http.StatusServiceUnavailable
	}
	if secret == "" {
		u.Log.Warn("upload: not verified", "path", r.URL.Path, "reason", "unknown LiveKit API key")
		return nil, http.StatusUnauthorized
	}
	_, grants, err := v.Verify(secret)
	if err != nil {
		u.Log.Warn("upload: not verified", "path", r.URL.Path, "reason", err.Error())
		return nil, http.StatusUnauthorized
	}
	if grants.Observability == nil || !grants.Observability.Write {
		u.Log.Warn("upload: not verified", "path", r.URL.Path, "reason", "token lacks the observability write grant")
		return nil, http.StatusForbidden
	}
	return keys, http.StatusOK
}

// appendEnvelope attributes e to the verified project and appends it, answering 503 on failure so
// the agent retries. It reports whether the envelope was appended.
func (u *Uploads) appendEnvelope(w http.ResponseWriter, r *http.Request, keys *projectKeys, e event.Envelope) bool {
	e.OrganisationID, e.ProjectID, e.ReceivedAt = keys.project.OrganisationID, keys.project.ID, u.Now()
	if _, err := u.Stream.Append(r.Context(), e); err != nil {
		u.Log.Error("upload: append failed", "kind", e.Kind, "project", keys.project.ID, "err", err)
		unavailable(w)
		return false
	}
	return true
}

// retryAfter is the delay offered to an agent when the stream or the project store is down.
const retryAfter = 2 * time.Second

// unavailable answers 503 with a google.rpc.Status carrying RetryInfo. The session uploader in
// LiveKit Agents 1.8.7 retries a 5xx only when it finds RetryInfo (_parse_retry_delay) and drops
// the report otherwise; OTLP over HTTP specifies the same body.
func unavailable(w http.ResponseWriter) {
	retry, err := anypb.New(&errdetails.RetryInfo{RetryDelay: durationpb.New(retryAfter)})
	var body []byte
	if err == nil {
		body, err = proto.Marshal(&status.Status{Code: int32(code.Code_UNAVAILABLE), Message: "stream unavailable", Details: []*anypb.Any{retry}})
	}
	if err != nil {
		http.Error(w, "stream unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/x-protobuf")
	w.WriteHeader(http.StatusServiceUnavailable)
	_, _ = w.Write(body)
}

// errTooLarge marks a body over its limit, answered 413.
var errTooLarge = errors.New("body too large")

func statusFor(err error) int {
	var mbe *http.MaxBytesError
	if errors.As(err, &mbe) || errors.Is(err, errTooLarge) {
		return http.StatusRequestEntityTooLarge
	}
	return http.StatusBadRequest
}

// readOTLP returns the export body, decompressed when the exporter gzipped it (the Agents
// exporters do).
func readOTLP(body io.Reader, encoding string) ([]byte, error) {
	switch encoding {
	case "", "identity":
	case "gzip":
		zr, err := gzip.NewReader(body)
		if err != nil {
			return nil, fmt.Errorf("gzip: %w", err)
		}
		defer func() { _ = zr.Close() }()
		body = zr
	default:
		return nil, fmt.Errorf("content encoding %q not supported", encoding)
	}
	b, err := io.ReadAll(io.LimitReader(body, maxOTLPDecoded+1))
	if err != nil {
		return nil, err
	}
	if len(b) > maxOTLPDecoded {
		return nil, errTooLarge
	}
	return b, nil
}

// readSessionReport reads the multipart parts by name. The header part is required and must be a
// MetricsRecordingHeader; unknown parts are skipped.
func readSessionReport(r *http.Request) (event.SessionReport, string, error) {
	var report event.SessionReport
	mt, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mt != "multipart/form-data" {
		return report, "", errors.New("not multipart/form-data")
	}
	mr, err := r.MultipartReader()
	if err != nil {
		return report, "", err
	}
	var header *livekit.MetricsRecordingHeader
	for {
		part, err := mr.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return report, "", err
		}
		switch part.FormName() {
		case "header":
			b, err := readPart(part)
			if err != nil {
				return report, "", err
			}
			header = &livekit.MetricsRecordingHeader{}
			if err := proto.Unmarshal(b, header); err != nil {
				return report, "", fmt.Errorf("header: %w", err)
			}
		case "chat_history":
			b, err := readPart(part)
			if err != nil {
				return report, "", err
			}
			if !json.Valid(b) {
				return report, "", errors.New("chat_history is not JSON")
			}
			report.ChatHistory = string(b)
		case "audio":
			if _, err := io.Copy(io.Discard, part); err != nil {
				return report, "", err
			}
			report.AudioDiscarded = true
		default:
			if _, err := io.Copy(io.Discard, part); err != nil {
				return report, "", err
			}
		}
	}
	if header == nil {
		return report, "", errors.New("no header part")
	}
	report.Header, err = protojson.Marshal(header)
	if err != nil {
		return report, "", fmt.Errorf("header: %w", err)
	}
	return report, header.GetJobId(), nil
}

func readPart(p io.Reader) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(p, maxSessionMetadata+1))
	if err != nil {
		return nil, err
	}
	if len(b) > maxSessionMetadata {
		return nil, errTooLarge
	}
	return b, nil
}
