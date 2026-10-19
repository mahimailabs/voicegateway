package ingest

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"strconv"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/proto"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// recordingProject owns testKey, with recordings on.
type recordingProject struct{ fixedProject }

func (recordingProject) ProjectByLiveKitKey(_ context.Context, key string) (store.Project, error) {
	if key != testKey {
		return store.Project{}, store.ErrNotFound
	}
	return store.Project{OrganisationID: "default", ID: "production", Recordings: true}, nil
}

// fakeAudio keeps what PutRecording received, or fails like a bucket that is down.
type fakeAudio struct {
	fail       bool
	job        string
	size       int64
	data       []byte
	calls      int
	storedName string
}

func (a *fakeAudio) PutRecording(_ context.Context, _ time.Time, _, _, jobID string, r io.Reader, size int64) (string, int64, error) {
	a.calls++
	if a.fail {
		return "", 0, errors.New("connection refused")
	}
	b, err := io.ReadAll(r)
	if err != nil {
		return "", 0, err
	}
	a.job, a.size, a.data = jobID, size, b
	return a.storedName, int64(len(b)), nil
}

// envelopes keeps every appended envelope.
type envelopes struct{ got []event.Envelope }

func (e *envelopes) Append(_ context.Context, env event.Envelope) (string, error) {
	e.got = append(e.got, env)
	return "1-0", nil
}

type part struct {
	name, file string
	data       []byte
}

// sessionUpload builds the multipart body LiveKit Agents sends (telemetry/traces.py), each part
// with its own Content-Length.
func sessionUpload(t *testing.T, parts ...part) ([]byte, string) {
	t.Helper()
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	for _, p := range parts {
		hdr := textproto.MIMEHeader{}
		hdr.Set("Content-Disposition", `form-data; name="`+p.name+`"; filename="`+p.file+`"`)
		hdr.Set("Content-Length", strconv.Itoa(len(p.data)))
		pw, err := mw.CreatePart(hdr)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := pw.Write(p.data); err != nil {
			t.Fatal(err)
		}
	}
	if err := mw.Close(); err != nil {
		t.Fatal(err)
	}
	return body.Bytes(), mw.FormDataContentType()
}

func headerPart(t *testing.T) part {
	t.Helper()
	b, err := proto.Marshal(&livekit.MetricsRecordingHeader{RoomId: "RM_test", JobId: "AJ_test"})
	if err != nil {
		t.Fatal(err)
	}
	return part{"header", "header.binpb", b}
}

// Phase 07a, acceptance check 1: with recordings on, the audio streams to the bucket under the
// header's job id, sized by the part's Content-Length, and only its key reaches the stream.
func TestUploadStoresRecording(t *testing.T) {
	audio := bytes.Repeat([]byte{1}, 1<<20)
	body, ctype := sessionUpload(t, headerPart(t), part{"audio", "recording.ogg", audio})
	bucket := &fakeAudio{storedName: "recordings/2026/10/19/default/production/AJ_test.ogg"}
	stream := &envelopes{}
	at := time.Date(2026, 10, 19, 1, 2, 3, 0, time.UTC)
	u := &Uploads{Projects: recordingProject{}, Stream: stream, Audio: bucket, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: func() time.Time { return at }}

	rec := send(http.HandlerFunc(u.SessionReport), bearer(t, testKey, testSecret, true), "", ctype, body)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if bucket.job != "AJ_test" || bucket.size != int64(len(audio)) || !bytes.Equal(bucket.data, audio) {
		t.Fatalf("bucket got job %q, size %d, %d bytes; want AJ_test with all %d", bucket.job, bucket.size, len(bucket.data), len(audio))
	}
	if len(stream.got) != 1 {
		t.Fatalf("appended %d envelopes, want 1", len(stream.got))
	}
	var got event.SessionReport
	if err := json.Unmarshal(stream.got[0].Payload, &got); err != nil {
		t.Fatal(err)
	}
	want := event.StoredRecording{Key: bucket.storedName, SizeBytes: int64(len(audio)), StoredAt: at}
	if got.AudioDiscarded || got.Recording == nil || *got.Recording != want {
		t.Fatalf("report recording = %+v, discarded %v; want %+v", got.Recording, got.AudioDiscarded, want)
	}
	if bytes.Contains(stream.got[0].Payload, []byte{1, 1, 1, 1}) {
		t.Fatal("audio bytes reached the stream")
	}
}

// A bucket that fails answers 503 with RetryInfo and appends nothing, so the agent sends the whole
// report again; an audio part before the header, which names it, is refused.
func TestUploadRecordingRefusals(t *testing.T) {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	good, goodType := sessionUpload(t, headerPart(t), part{"audio", "recording.ogg", []byte("ogg")})
	early, earlyType := sessionUpload(t, part{"audio", "recording.ogg", []byte("ogg")}, headerPart(t))
	cases := []struct {
		name        string
		bucket      *fakeAudio
		body        []byte
		ctype       string
		status, put int
	}{
		{"bucket down", &fakeAudio{fail: true}, good, goodType, http.StatusServiceUnavailable, 1},
		{"audio before header", &fakeAudio{}, early, earlyType, http.StatusBadRequest, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			stream := &envelopes{}
			u := &Uploads{Projects: recordingProject{}, Stream: stream, Audio: c.bucket, Log: log, Now: time.Now}
			rec := send(http.HandlerFunc(u.SessionReport), bearer(t, testKey, testSecret, true), "", c.ctype, c.body)
			if rec.Code != c.status || c.bucket.calls != c.put || len(stream.got) != 0 {
				t.Fatalf("status %d, %d puts, %d appended; want %d, %d puts, none appended", rec.Code, c.bucket.calls, len(stream.got), c.status, c.put)
			}
		})
	}
}

// With recordings off the bucket is never called and the report says the audio was discarded.
func TestUploadRecordingsOffDiscards(t *testing.T) {
	body, ctype := sessionUpload(t, headerPart(t), part{"audio", "recording.ogg", []byte("ogg")})
	bucket, stream := &fakeAudio{}, &envelopes{}
	u := &Uploads{Projects: fixedProject{}, Stream: stream, Audio: bucket, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}
	if rec := send(http.HandlerFunc(u.SessionReport), bearer(t, testKey, testSecret, true), "", ctype, body); rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	var got event.SessionReport
	if len(stream.got) != 1 || json.Unmarshal(stream.got[0].Payload, &got) != nil {
		t.Fatalf("appended %d envelopes, want 1 session report", len(stream.got))
	}
	if bucket.calls != 0 || !got.AudioDiscarded || got.Recording != nil {
		t.Fatalf("%d puts, discarded %v, recording %+v; want none stored", bucket.calls, got.AudioDiscarded, got.Recording)
	}
}
