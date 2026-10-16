package ingest

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"strings"
	"testing"
	"time"

	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// bearer returns the Authorization value a LiveKit agent sends: a JWT from its key and secret with
// the observability write grant (livekit/agents 1.8.7 telemetry/traces.py, _AuthHeaderProvider).
func bearer(t *testing.T, key, secret string, write bool) string {
	t.Helper()
	at := auth.NewAccessToken(key, secret).SetValidFor(time.Hour)
	if write {
		at.SetObservabilityGrant(&auth.ObservabilityGrant{Write: true})
	}
	jwt, err := at.ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	return "Bearer " + jwt
}

func gzipped(t *testing.T, b []byte) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	if _, err := zw.Write(b); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// An OTLP export is appended only with a valid token from the project's key carrying the write
// grant; it is stored decompressed, attributed, with no invented event id.
func TestUploadOTLPVerified(t *testing.T) {
	f := newFixture(t)
	export := []byte("\x0a\x00") // an ExportTraceServiceRequest with one empty ResourceSpans
	h := f.uploads.OTLP(event.KindAgentTraces)
	cases := []struct {
		name   string
		authz  string
		status int
	}{
		{"no token", "", http.StatusUnauthorized},
		{"not bearer", strings.TrimPrefix(bearer(t, testKey, testSecret, true), "Bearer "), http.StatusUnauthorized},
		{"unknown key", bearer(t, "APIother", testSecret, true), http.StatusUnauthorized},
		{"wrong secret", bearer(t, testKey, "another-secret-that-is-long-enough", true), http.StatusUnauthorized},
		{"no write grant", bearer(t, testKey, testSecret, false), http.StatusForbidden},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rec := send(h, c.authz, "gzip", "application/x-protobuf", gzipped(t, export))
			if rec.Code != c.status {
				t.Fatalf("status = %d, want %d", rec.Code, c.status)
			}
			if n := f.rdb.XLen(context.Background(), stream.Ingest).Val(); n != 0 {
				t.Fatalf("stream length = %d, want 0", n)
			}
		})
	}

	rec := send(h, bearer(t, testKey, testSecret, true), "gzip", "application/x-protobuf", gzipped(t, export))
	if rec.Code != http.StatusOK || rec.Body.Len() != 0 {
		t.Fatalf("valid export: status = %d, body %d bytes, want 200 with an empty response", rec.Code, rec.Body.Len())
	}
	e := f.only(t)
	if e.Kind != event.KindAgentTraces || e.ProjectID != "production" || e.ID != "" || !bytes.Equal(e.Payload, export) {
		t.Fatalf("envelope = %+v, want decompressed traces for production with no id", e)
	}
}

// A body that does not decompress, or decompresses past the limit, is refused and not appended.
func TestUploadOTLPBadBody(t *testing.T) {
	f := newFixture(t)
	h := f.uploads.OTLP(event.KindAgentLogs)
	ok := bearer(t, testKey, testSecret, true)
	if rec := send(h, ok, "gzip", "application/x-protobuf", []byte("not gzip")); rec.Code != http.StatusBadRequest {
		t.Fatalf("bad gzip: status = %d, want 400", rec.Code)
	}
	bomb := gzipped(t, make([]byte, maxOTLPDecoded+1))
	if rec := send(h, ok, "gzip", "application/x-protobuf", bomb); rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized after decompression: status = %d, want 413", rec.Code)
	}
	if n := f.rdb.XLen(context.Background(), stream.Ingest).Val(); n != 0 {
		t.Fatalf("stream length = %d, want 0", n)
	}
}

// A session upload keeps the header and chat history, discards the audio without holding it, and
// is identified by its job id.
func TestUploadSessionReport(t *testing.T) {
	f := newFixture(t)
	header, err := proto.Marshal(&livekit.MetricsRecordingHeader{
		RoomId: "RM_test", JobId: "AJ_test", StartTime: timestamppb.New(time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)),
	})
	if err != nil {
		t.Fatal(err)
	}
	chat := `{"items":[{"type":"message","role":"user","content":["hello"]}]}`

	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	for _, p := range []struct {
		name, file, ctype string
		data              []byte
	}{
		{"header", "header.binpb", "application/protobuf", header},
		{"chat_history", "chat_history.json", "application/json", []byte(chat)},
		{"audio", "recording.ogg", "audio/ogg", bytes.Repeat([]byte{1}, 1<<20)},
	} {
		hdr := textproto.MIMEHeader{}
		hdr.Set("Content-Disposition", `form-data; name="`+p.name+`"; filename="`+p.file+`"`)
		hdr.Set("Content-Type", p.ctype)
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

	if rec := send(http.HandlerFunc(f.uploads.SessionReport), bearer(t, testKey, testSecret, false), "", mw.FormDataContentType(), body.Bytes()); rec.Code != http.StatusForbidden {
		t.Fatalf("no write grant: status = %d, want 403", rec.Code)
	}
	rec := send(http.HandlerFunc(f.uploads.SessionReport), bearer(t, testKey, testSecret, true), "", mw.FormDataContentType(), body.Bytes())
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	e := f.only(t)
	if e.Kind != event.KindAgentSessionReport || e.ID != "AJ_test" || e.ProjectID != "production" {
		t.Fatalf("envelope = %+v, want the session report of AJ_test for production", e)
	}
	var got event.SessionReport
	if err := json.Unmarshal(e.Payload, &got); err != nil {
		t.Fatal(err)
	}
	if !got.AudioDiscarded || string(got.ChatHistory) != chat || !strings.Contains(string(got.Header), `"roomId":"RM_test"`) {
		t.Fatalf("report = header %s, chat %s, audio discarded %v", got.Header, got.ChatHistory, got.AudioDiscarded)
	}
	if bytes.Contains(e.Payload, []byte{1, 1, 1, 1}) {
		t.Fatal("audio bytes reached the stream")
	}
}

func send(h http.Handler, authz, encoding, ctype string, body []byte) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/observability/test/v0", bytes.NewReader(body))
	req.Header.Set("Content-Type", ctype)
	if encoding != "" {
		req.Header.Set("Content-Encoding", encoding)
	}
	if authz != "" {
		req.Header.Set("Authorization", authz)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}
