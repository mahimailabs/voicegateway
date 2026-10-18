package api_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/rpc"
	"github.com/livekit/protocol/utils/protojson"
	"github.com/livekit/psrpc/pkg/bus/redisbus"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/pb33f/libopenapi"
	validator "github.com/pb33f/libopenapi-validator"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

func (f *fixture) egressScraper() *scrape.EgressScraper {
	return &scrape.EgressScraper{
		Projects: f.projects, Samples: f.layers, Lock: scrape.RedisLock{Client: f.rdb}, Dial: scrape.DialSDK,
		Log: quiet(), Now: now, Every: every, Timeout: 5 * time.Second,
	}
}

func (f *fixture) ingressScraper() *scrape.IngressScraper {
	return &scrape.IngressScraper{
		Projects: f.projects, Samples: f.layers, Lock: scrape.RedisLock{Client: f.rdb}, Dial: scrape.DialSDK,
		Log: quiet(), Now: now, Every: every, Timeout: 5 * time.Second,
	}
}

// ioService plays LiveKit's egress and ingress services against the real server: livekit-server
// --dev runs neither, and an egress or ingress exists only once one of them reports it. It reports
// through LiveKit's own IOInfo client over the server's Redis message bus, as livekit/egress and
// livekit/ingress do, so ListEgress and ListIngress answer from the server's own store.
func (f *fixture) ioService(t *testing.T) rpc.IOInfoClient {
	t.Helper()
	c, err := rpc.NewIOInfoClient(redisbus.New(f.rdb))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.Close)
	return c
}

// storeWebhook stores a LiveKit webhook in ClickHouse events, as the worker's sink does, with the
// protojson body LiveKit sends.
func (f *fixture) storeWebhook(t *testing.T, project string, ev *livekit.WebhookEvent) {
	t.Helper()
	body, err := protojson.Marshal(ev)
	if err != nil {
		t.Fatal(err)
	}
	if err := f.layers.Insert(context.Background(), []store.Event{{
		OrganisationID: org, ProjectID: project, Kind: string(event.KindLiveKitWebhook),
		DedupeKey: ev.GetId(), EventID: ev.GetId(), StreamID: ev.GetId(), ReceivedAt: time.Now(), Payload: body,
	}}); err != nil {
		t.Fatal(err)
	}
}

// egressRead is the contract's Egress, as much of it as these tests read.
type egressRead struct {
	Egress []struct {
		store.Egress
		Outcome *string          `json:"outcome"`
		History []store.StatusAt `json:"history"`
		Sampled *bool            `json:"sampled"`
	} `json:"egress"`
	Rejected     []json.RawMessage `json:"rejected"`
	SourceStatus *struct {
		LastGoodReadAt *time.Time `json:"lastGoodReadAt"`
		ErrorClass     *string    `json:"errorClass"`
	} `json:"sourceStatus"`
}

// Acceptance check 8.
func TestEgressLive(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.addProject("p1", devSecret)
	s := f.egressScraper()
	svc := f.ioService(t)

	code, body := f.getValid(t, "p1", "egress")
	if code != http.StatusServiceUnavailable || !strings.Contains(string(body), "source_unavailable") {
		t.Fatalf("a project never read answered %d: %s", code, body)
	}

	// Every secret an egress request carries, and the caller's number in the room name. The bucket's
	// name is not a secret: the contract shows the file's bucket host.
	secrets := []string{
		"s3-access-3e9f", "s3-secret-71ab", "s3-session-0c2d", "s3-external-9a4e", "gcp-credentials-5b8c",
		"azure-account-2d7f", "azure-key-6e1a", "proxy-user-0d3e", "proxy-pass-1a2b", "stream-key-5f6e",
		"stream-key-8a7b", "hook-signing-3b4a", "5551234567",
	}
	streamURL := "rtmps://user-6b:stream-key-8a7b@live.example.test/app/stream-key-5f6e?token=stream-key-8a7b"
	request := &livekit.EgressInfo_RoomComposite{RoomComposite: &livekit.RoomCompositeEgressRequest{
		RoomName: "call-+15551234567", AudioOnly: true,
		FileOutputs: []*livekit.EncodedFileOutput{{FileType: livekit.EncodedFileType_OGG, Output: &livekit.EncodedFileOutput_S3{S3: &livekit.S3Upload{
			AccessKey: "s3-access-3e9f", Secret: "s3-secret-71ab", SessionToken: "s3-session-0c2d", AssumeRoleExternalId: "s3-external-9a4e",
			Bucket: "bucket-name-77c1", Proxy: &livekit.ProxyConfig{Url: "http://proxy.example.test", Username: "proxy-user-0d3e", Password: "proxy-pass-1a2b"},
		}}}},
		SegmentOutputs: []*livekit.SegmentedFileOutput{{Output: &livekit.SegmentedFileOutput_Gcp{Gcp: &livekit.GCPUpload{Credentials: "gcp-credentials-5b8c"}}}},
		ImageOutputs:   []*livekit.ImageOutput{{Output: &livekit.ImageOutput_Azure{Azure: &livekit.AzureBlobUpload{AccountName: "azure-account-2d7f", AccountKey: "azure-key-6e1a"}}}},
		StreamOutputs:  []*livekit.StreamOutput{{Protocol: livekit.StreamProtocol_RTMP, Urls: []string{streamURL}}},
		Webhooks:       []*livekit.WebhookConfig{{Url: "https://hooks.example.test/h", SigningKey: "hook-signing-3b4a"}},
	}}

	// LiveKit's egress service starts the egress, reports it active with its stream, then hits the
	// time limit. The webhooks for the first two are stored; the end is seen only by the samples.
	created := time.Now().Truncate(time.Microsecond)
	limited := &livekit.EgressInfo{
		EgressId: "EG_limited", RoomId: "RM_egress", RoomName: "call-+15551234567", Status: livekit.EgressStatus_EGRESS_STARTING,
		UpdatedAt: created.UnixNano(), Request: request,
	}
	if _, err := svc.CreateEgress(ctx, limited); err != nil {
		t.Fatalf("create egress: %v", err)
	}
	f.storeWebhook(t, "p1", &livekit.WebhookEvent{Event: "egress_started", Id: "EV_E1", CreatedAt: created.Unix(), EgressInfo: limited})
	started := time.Now().Truncate(time.Microsecond)
	limited.Status, limited.StartedAt, limited.UpdatedAt = livekit.EgressStatus_EGRESS_ACTIVE, started.UnixNano(), started.UnixNano()
	limited.StreamResults = []*livekit.StreamInfo{{Url: streamURL, StartedAt: started.UnixNano(), Status: livekit.StreamInfo_ACTIVE}}
	if _, err := svc.UpdateEgress(ctx, limited); err != nil {
		t.Fatal(err)
	}
	f.storeWebhook(t, "p1", &livekit.WebhookEvent{Event: "egress_updated", Id: "EV_E2", CreatedAt: started.Unix(), EgressInfo: limited})
	s.Tick(ctx)

	limited.SetLimitReached()
	limited.SetEndReason(livekit.EndReasonLimitReached)
	limited.StreamResults[0].Status, limited.StreamResults[0].EndedAt = livekit.StreamInfo_FINISHED, limited.EndedAt
	limited.FileResults = []*livekit.FileInfo{{
		Filename: "call-+15551234567.ogg", Size: 52_000, Duration: int64(3 * time.Second),
		Location: "https://bucket-name-77c1.s3.example.test/call.ogg?X-Amz-Signature=s3-secret-71ab",
	}}
	if _, err := svc.UpdateEgress(ctx, limited); err != nil {
		t.Fatal(err)
	}
	// A second egress whose stream fails and takes the egress down, naming its URL in the error.
	failed := &livekit.EgressInfo{
		EgressId: "EG_failed", RoomName: "lobby", Status: livekit.EgressStatus_EGRESS_STARTING, UpdatedAt: time.Now().UnixNano(), Request: request,
	}
	if _, err := svc.CreateEgress(ctx, failed); err != nil {
		t.Fatal(err)
	}
	failed.StreamResults = []*livekit.StreamInfo{{Url: streamURL, Status: livekit.StreamInfo_FAILED, Error: "dial " + streamURL + ": connection refused"}}
	failed.SetFailed(&streamError{streamURL})
	if _, err := svc.UpdateEgress(ctx, failed); err != nil {
		t.Fatal(err)
	}
	listed, err := lksdk.NewEgressClient(f.url, devKey, devSecret).ListEgress(ctx, &livekit.ListEgressRequest{})
	if err != nil || len(listed.GetItems()) != 2 {
		t.Fatalf("ListEgress = %d items, %v; want the two egress reported", len(listed.GetItems()), err)
	}
	time.Sleep(every)
	s.Tick(ctx)

	code, body = f.getValid(t, "p1", "egress")
	if code != http.StatusOK {
		t.Fatalf("egress answered %d: %s", code, body)
	}
	var got egressRead
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Egress) != 2 || got.Rejected == nil || len(got.Rejected) != 0 {
		t.Fatalf("egress = %d, rejected = %v; want 2 and none", len(got.Egress), got.Rejected)
	}
	byID := map[string]int{}
	for i, e := range got.Egress {
		byID[e.ID] = i
	}
	lim := got.Egress[byID["EG_limited"]]
	if lim.Status != "EGRESS_LIMIT_REACHED" || lim.Outcome == nil || *lim.Outcome != "cut_short" {
		t.Errorf("limited egress status %s, outcome %v; want EGRESS_LIMIT_REACHED read as cut_short", lim.Status, lim.Outcome)
	}
	var statuses []string
	for _, h := range lim.History {
		statuses = append(statuses, h.Status)
	}
	if !slices.Equal(statuses, []string{"EGRESS_STARTING", "EGRESS_ACTIVE", "EGRESS_LIMIT_REACHED"}) {
		t.Fatalf("history = %v, want starting and active from the webhooks, then the limit from the samples", statuses)
	}
	if !sameTime(lim.History[0].At, &created) || !sameTime(lim.History[1].At, &started) || lim.History[2].At == nil || !lim.History[2].At.After(started) {
		t.Errorf("history times = %v %v %v, want the webhooks' %v and %v, then a sample after", lim.History[0].At, lim.History[1].At, lim.History[2].At, created, started)
	}
	if !sameTime(lim.StartedAt, &started) || !sameTime(lim.EndedAt, nanosTime(limited.GetEndedAt())) {
		t.Errorf("startedAt %v, endedAt %v; want %v and %v", lim.StartedAt, lim.EndedAt, started, nanosTime(limited.GetEndedAt()))
	}
	if len(lim.Streams) != 1 || lim.Streams[0].Host == nil || *lim.Streams[0].Host != "rtmps://live.example.test" ||
		lim.Streams[0].Status != "FINISHED" || !sameTime(lim.Streams[0].StartedAt, &started) || lim.Streams[0].EndedAt == nil {
		t.Errorf("streams = %+v, want one finished stream to rtmps://live.example.test", lim.Streams)
	}
	if lim.Sampled == nil || !*lim.Sampled || lim.Room != "call-•••• 4567" {
		t.Errorf("sampled %v, room %q; want sampled while running and the number masked", lim.Sampled, lim.Room)
	}
	if lim.File == nil || lim.File.SizeBytes == nil || *lim.File.SizeBytes != 52_000 || lim.File.DurationS == nil || *lim.File.DurationS != 3 ||
		lim.File.BucketHost == nil || lim.File.Playable != nil {
		t.Errorf("file = %+v", lim.File)
	}
	if r := lim.Request; r.Kind != "roomComposite" || r.AudioOnly == nil || !*r.AudioOnly || r.FileType != "OGG" || r.Storage != "s3" {
		t.Errorf("request = %+v", r)
	}
	fail := got.Egress[byID["EG_failed"]]
	if fail.Outcome == nil || *fail.Outcome != "lost" || len(fail.Streams) != 1 || fail.Streams[0].Status != "FAILED" ||
		fail.Streams[0].Error == nil || !strings.Contains(*fail.Streams[0].Error, "rtmps://live.example.test") || fail.Error == nil {
		t.Errorf("failed egress = %+v, want lost with its failed stream named by host", fail)
	}
	if fail.Sampled == nil || *fail.Sampled {
		t.Errorf("sampled = %v, want false: every sample saw it ended", fail.Sampled)
	}
	if st := got.SourceStatus; st == nil || st.ErrorClass != nil || st.LastGoodReadAt == nil {
		t.Errorf("sourceStatus = %+v, want a good last read", st)
	}

	stored := f.storedRows(t, "egress_samples", "egress")
	for _, secret := range secrets {
		if strings.Contains(string(body), secret) {
			t.Errorf("the read serves %q", secret)
		}
		if strings.Contains(stored, secret) {
			t.Errorf("egress_samples stores %q", secret)
		}
	}
}

type streamError struct{ url string }

func (e *streamError) Error() string { return "stream " + e.url + " failed" }

// ingressRead is the contract's Ingress, as much of it as these tests read.
type ingressRead struct {
	Ingress []struct {
		store.Ingress
		CreatedAt *time.Time       `json:"createdAt"`
		History   []store.StatusAt `json:"history"`
	} `json:"ingress"`
	SourceStatus *struct {
		ErrorClass *string `json:"errorClass"`
	} `json:"sourceStatus"`
}

// Acceptance check 9.
func TestIngressLive(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.addProject("p1", devSecret)
	s := f.ingressScraper()
	svc := f.ioService(t)

	code, body := f.getValid(t, "p1", "ingress")
	if code != http.StatusServiceUnavailable || !strings.Contains(string(body), "source_unavailable") {
		t.Fatalf("a project never read answered %d: %s", code, body)
	}

	secrets := []string{
		"stream-key-4e7d91", "url-user-2c8a", "url-pass-6f1b", "query-token-9d3e", "path-token-3f8b", "5557654321", "5553334444",
		"metadata-secret-5a1c", "Participant Name 8b2e",
	}
	info := &livekit.IngressInfo{
		IngressId: "IN_live", Name: "studio feed", StreamKey: "stream-key-4e7d91",
		Url:       "rtmp://url-user-2c8a:url-pass-6f1b@ingest.example.test/path-token-3f8b/stream-key-4e7d91?token=query-token-9d3e",
		InputType: livekit.IngressInput_RTMP_INPUT, RoomName: "call-+15557654321", ParticipantIdentity: "feed",
		ParticipantName: "Participant Name 8b2e", ParticipantMetadata: `{"secret":"metadata-secret-5a1c"}`, Reusable: true, EnableTranscoding: ptrTo(true),
		State: &livekit.IngressState{Status: livekit.IngressState_ENDPOINT_INACTIVE},
	}
	if _, err := svc.CreateIngress(ctx, info); err != nil {
		t.Fatalf("create ingress: %v", err)
	}
	s.Tick(ctx)

	// The encoder connects: LiveKit's ingress service reports it publishing, and its webhook is stored.
	publishing := time.Now().Truncate(time.Microsecond)
	state := &livekit.IngressState{
		Status: livekit.IngressState_ENDPOINT_PUBLISHING, StartedAt: publishing.UnixNano(), UpdatedAt: publishing.UnixNano(), RoomId: "RM_ingress",
		// LiveKit's text can repeat what the encoder sent: the key, the URL with its credentials, a number.
		Error:  "auth url-user-2c8a:url-pass-6f1b for stream-key-4e7d91 at rtmp://url-user-2c8a:url-pass-6f1b@ingest.example.test/path-token-3f8b/stream-key-4e7d91?token=query-token-9d3e from +15553334444",
		Audio:  &livekit.InputAudioState{MimeType: "audio/aac", AverageBitrate: 128_000, Channels: 2, SampleRate: 48_000},
		Tracks: []*livekit.TrackInfo{{Sid: "TR_1", Type: livekit.TrackType_AUDIO, MimeType: "audio/opus", Source: livekit.TrackSource_MICROPHONE}},
	}
	if _, err := svc.UpdateIngressState(ctx, &rpc.UpdateIngressStateRequest{IngressId: "IN_live", State: state}); err != nil {
		t.Fatal(err)
	}
	info.State = state
	f.storeWebhook(t, "p1", &livekit.WebhookEvent{Event: "ingress_started", Id: "EV_I1", CreatedAt: publishing.Unix(), IngressInfo: info})
	time.Sleep(every)
	s.Tick(ctx)

	code, body = f.getValid(t, "p1", "ingress")
	if code != http.StatusOK {
		t.Fatalf("ingress answered %d: %s", code, body)
	}
	var got ingressRead
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Ingress) != 1 {
		t.Fatalf("ingress = %d, want 1: %s", len(got.Ingress), body)
	}
	in := got.Ingress[0]
	if in.URL != "rtmp://ingest.example.test" {
		t.Errorf("url = %q, want scheme and host only", in.URL)
	}
	if in.CreatedAt != nil || !strings.Contains(string(body), `"createdAt":null`) {
		t.Errorf("createdAt = %v, want null: IngressInfo has no creation time", in.CreatedAt)
	}
	if in.Error == nil || !strings.Contains(*in.Error, "rtmp://ingest.example.test") || !strings.Contains(*in.Error, "•••• 4444") {
		t.Errorf("error = %v, want the URL by host and the number masked", in.Error)
	}
	if in.Status != "ENDPOINT_PUBLISHING" || !sameTime(in.StartedAt, &publishing) || in.EndedAt != nil || in.Room != "call-•••• 4321" {
		t.Errorf("status %s, startedAt %v, endedAt %v, room %q", in.Status, in.StartedAt, in.EndedAt, in.Room)
	}
	if in.AudioIn == nil || in.AudioIn.SampleRate == nil || *in.AudioIn.SampleRate != 48_000 || in.VideoIn != nil ||
		len(in.Published) != 1 || in.Published[0].Source != "MICROPHONE" {
		t.Errorf("audioIn %+v, videoIn %+v, published %+v", in.AudioIn, in.VideoIn, in.Published)
	}
	if in.Transcoding == nil || !*in.Transcoding || in.Reusable == nil || !*in.Reusable || in.Enabled != nil {
		t.Errorf("transcoding %v, reusable %v, enabled %v; want true, true and null (not reported)", in.Transcoding, in.Reusable, in.Enabled)
	}
	var statuses []string
	for _, h := range in.History {
		statuses = append(statuses, h.Status)
	}
	if !slices.Equal(statuses, []string{"ENDPOINT_INACTIVE", "ENDPOINT_PUBLISHING"}) || !sameTime(in.History[1].At, &publishing) {
		t.Errorf("history = %+v, want inactive from the samples, then publishing at the webhook's time %v", in.History, publishing)
	}
	if got.SourceStatus == nil || got.SourceStatus.ErrorClass != nil {
		t.Errorf("sourceStatus = %+v, want a good last read", got.SourceStatus)
	}

	stored := f.storedRows(t, "ingress_samples", "ingress")
	for _, secret := range secrets {
		if strings.Contains(string(body), secret) {
			t.Errorf("the read serves %q", secret)
		}
		if strings.Contains(stored, secret) {
			t.Errorf("ingress_samples stores %q", secret)
		}
	}
}

func ptrTo[T any](v T) *T { return &v }

// storedRows returns every stored row of a sample table, as ClickHouse holds it.
func (f *fixture) storedRows(t *testing.T, table, column string) string {
	t.Helper()
	opts, err := clickhouse.ParseDSN(f.chURL)
	if err != nil {
		t.Fatal(err)
	}
	db := clickhouse.OpenDB(opts)
	defer func() { _ = db.Close() }()
	rows, err := db.QueryContext(context.Background(), `SELECT `+column+` FROM `+table) //nolint:gosec // table and column are the tests' own constants
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = rows.Close() }()
	var all strings.Builder
	for rows.Next() {
		var c string
		if err := rows.Scan(&c); err != nil {
			t.Fatal(err)
		}
		all.WriteString(c)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	if all.Len() == 0 {
		t.Fatalf("nothing was stored in %s", table)
	}
	return all.String()
}

var (
	contract     validator.Validator
	contractErr  error
	contractOnce sync.Once
)

// getValid answers one of a project's reads through the real gate, as getRead does, and checks the
// response against docs/api/openapi.yaml.
func (f *fixture) getValid(t *testing.T, project, read string) (int, []byte) {
	t.Helper()
	contractOnce.Do(func() {
		spec, err := os.ReadFile("../../docs/api/openapi.yaml")
		if err != nil {
			contractErr = err
			return
		}
		doc, err := libopenapi.NewDocument(spec)
		if err != nil {
			contractErr = err
			return
		}
		v, errs := validator.NewValidator(doc)
		if len(errs) > 0 {
			contractErr = errs[0]
			return
		}
		contract = v
	})
	if contractErr != nil {
		t.Fatal(contractErr)
	}
	req := httptest.NewRequest(http.MethodGet, "/api/v1/org/"+org+"/project/"+project+"/"+read, nil)
	gate, key := api.KeyFor(org, project)
	req.Header.Set("Authorization", "Bearer "+key)
	rec := httptest.NewRecorder()
	(&api.API{Projects: f.projects, Layers: f.layers, Log: quiet(), Now: time.Now, Gate: gate}).Routes().ServeHTTP(rec, req)
	body := rec.Body.Bytes()
	res := rec.Result()
	res.Body = io.NopCloser(bytes.NewReader(body))
	if ok, errs := contract.ValidateHttpResponse(req, res); !ok {
		for _, e := range errs {
			t.Errorf("contract: %s: %s", e.Message, e.Reason)
			for _, se := range e.SchemaValidationErrors {
				t.Errorf("  at %s: %s", se.FieldPath, se.Reason)
			}
		}
	}
	return res.StatusCode, body
}
