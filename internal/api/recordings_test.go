package api

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

// oneRecording holds one recording of default/production.
type oneRecording struct{ rec store.Recording }

func (o oneRecording) Recording(_ context.Context, org, project, id string) (store.Recording, error) {
	if org != "default" || project != "production" || id != o.rec.ID {
		return store.Recording{}, store.ErrNotFound
	}
	return o.rec, nil
}

// bucketMedia opens objects from a map, or fails like a bucket that is down.
type bucketMedia struct {
	objects map[string][]byte
	down    bool
}

type nopCloser struct{ *bytes.Reader }

func (nopCloser) Close() error { return nil }

func (b bucketMedia) Open(_ context.Context, _, _, key string) (io.ReadSeekCloser, error) {
	if b.down {
		return nil, errors.New("connection refused")
	}
	data, ok := b.objects[key]
	if !ok {
		return nil, store.ErrNotFound
	}
	return nopCloser{bytes.NewReader(data)}, nil
}

// Phase 07a, acceptance check 5: getRecordingMedia serves the stored audio, whole or by range,
// privately; an unknown or expired id, a missing object and a non-member are refused, and a bucket
// that is down is a 503.
func TestGetRecordingMedia(t *testing.T) {
	now := time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
	audio := []byte("OggS-the-call-audio")
	rec := store.Recording{ID: "8d2c6f1e-0000-4000-8000-000000000001", ObjectKey: "recordings/k.ogg", StoredAt: now.Add(-time.Hour)}
	expired := rec
	expired.StoredAt = now.Add(-store.RecordingKeep - time.Hour)
	path := "/api/v1/org/default/project/production/recordings/" + rec.ID
	log := slog.New(slog.NewTextHandler(io.Discard, nil))

	cases := []struct {
		name    string
		rec     store.Recording
		media   bucketMedia
		path    string
		rangeHd string
		status  int
		body    string
	}{
		{"whole", rec, bucketMedia{objects: map[string][]byte{rec.ObjectKey: audio}}, path, "", http.StatusOK, string(audio)},
		{"range", rec, bucketMedia{objects: map[string][]byte{rec.ObjectKey: audio}}, path, "bytes=5-7", http.StatusPartialContent, "the"},
		{"unknown id", rec, bucketMedia{}, "/api/v1/org/default/project/production/recordings/other", "", http.StatusNotFound, ""},
		{"other project", rec, bucketMedia{}, "/api/v1/org/default/project/staging/recordings/" + rec.ID, "", http.StatusNotFound, ""},
		{"not a member", rec, bucketMedia{}, "/api/v1/org/acme/project/production/recordings/" + rec.ID, "", http.StatusForbidden, ""},
		{"expired", expired, bucketMedia{objects: map[string][]byte{rec.ObjectKey: audio}}, path, "", http.StatusNotFound, ""},
		{"object gone", rec, bucketMedia{objects: map[string][]byte{}}, path, "", http.StatusNotFound, ""},
		{"bucket down", rec, bucketMedia{down: true}, path, "", http.StatusServiceUnavailable, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			a := &API{Recordings: oneRecording{c.rec}, Media: c.media, Log: log, Now: func() time.Time { return now }, Gate: passThrough}
			req := httptest.NewRequest(http.MethodGet, c.path, nil)
			if c.rangeHd != "" {
				req.Header.Set("Range", c.rangeHd)
			}
			w := httptest.NewRecorder()
			a.Routes().ServeHTTP(w, req)
			if w.Code != c.status {
				t.Fatalf("status = %d, want %d: %s", w.Code, c.status, w.Body.String())
			}
			if c.body == "" {
				return
			}
			h := w.Header()
			if w.Body.String() != c.body || h.Get("Content-Type") != "audio/ogg" || h.Get("Accept-Ranges") != "bytes" || !strings.HasPrefix(h.Get("Cache-Control"), "private") {
				t.Fatalf("body %q, headers %v", w.Body.String(), h)
			}
		})
	}
}

type anyProject struct{}

func (anyProject) Project(_ context.Context, org, id string) (store.Project, error) {
	return store.Project{OrganisationID: org, ID: id}, nil
}

// recordedCall serves one call; the list methods are not used.
type recordedCall struct {
	Calls
	call store.Call
}

func (r recordedCall) Call(context.Context, string, string, string) (store.Call, error) {
	return r.call, nil
}

// A recorded call carries its recording, linked to getRecordingMedia, with its own clock; what is
// not extracted from the audio is null, and the body matches the contract.
func TestGetCallRecording(t *testing.T) {
	start := time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
	recStart := start.Add(1500 * time.Millisecond)
	f := &fixture{t: t}
	for _, c := range []struct {
		name     string
		recStart *time.Time
		align    string
	}{{"start reported", &recStart, alignInferred}, {"start unknown", nil, alignUnknown}} {
		t.Run(c.name, func(t *testing.T) {
			f.t = t
			call := store.Call{
				ID: "11111111-0000-4000-8000-000000000001", RoomName: "room", StartedAt: &start, EndState: "open", Servable: true, Summarized: true,
				Recording: &store.Recording{ID: "22222222-0000-4000-8000-000000000002", StartedAt: c.recStart},
			}
			a := &API{Projects: anyProject{}, Calls: recordedCall{call: call}, Traces: emptyCallTrace{}, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now, Gate: passThrough}
			var got struct {
				Recording *struct {
					URL      string   `json:"url"`
					Duration *float64 `json:"duration"`
					ID       string   `json:"id"`
					ClockID  string   `json:"clockId"`
				} `json:"recording"`
				Clocks []clock `json:"clocks"`
			}
			if status := f.get(a, "/api/v1/org/default/project/production/calls/"+call.ID, &got); status != http.StatusOK {
				t.Fatalf("status = %d", status)
			}
			r := got.Recording
			if r == nil || r.URL != "/api/v1/org/default/project/production/recordings/"+call.Recording.ID || r.Duration != nil || r.ClockID != clockRecording {
				t.Fatalf("recording = %+v", r)
			}
			last := got.Clocks[len(got.Clocks)-1]
			if last.ID != clockRecording || last.Origin != "recording_start" || last.Alignment != c.align {
				t.Fatalf("recording clock = %+v", last)
			}
			if c.recStart != nil && (last.Offset == nil || *last.Offset != 1.5) {
				t.Fatalf("recording clock offset = %v, want 1.5", last.Offset)
			}
		})
	}
}

// emptyCallTrace is a call with no spans.
type emptyCallTrace struct{}

func (emptyCallTrace) CallTrace(context.Context, string, string, string) ([]store.Span, turn.Result, error) {
	return nil, turn.Result{}, nil
}
