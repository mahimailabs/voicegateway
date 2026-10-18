package scrape

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/livekit/protocol/livekit"
)

func TestStreamHost(t *testing.T) {
	tests := []struct {
		in   string
		want *string
	}{
		{"rtmp://a.rtmp.youtube.com/live2/abcd-efgh-ijkl-mnop", ptr("rtmp://a.rtmp.youtube.com")},
		{"rtmps://user:pass-77@live.example.test:443/app/key-91?token=q-55", ptr("rtmps://live.example.test:443")},
		{"rtmp://live.example.test/app/key-91 live=1", ptr("rtmp://live.example.test")},
		{"srt://srt.example.test:9000?streamid=publish:key-91", ptr("srt://srt.example.test:9000")},
		{"wss://socket.example.test/audio?auth=key-91", ptr("wss://socket.example.test")},
		{"mux://key-91-mux", ptr("mux://")},
		{"twitch://key-91-twitch", ptr("twitch://")},
		{"not a url", nil},
		{"", nil},
	}
	for _, tt := range tests {
		got := streamHost(tt.in)
		if !equal(got, tt.want) {
			t.Errorf("streamHost(%q) = %v, want %v", tt.in, deref(got), deref(tt.want))
		}
	}
}

func TestIngressURL(t *testing.T) {
	tests := []struct {
		name, url, want string
	}{
		{"rtmp base url", "rtmps://project.rtmp.livekit.cloud/x", "rtmps://project.rtmp.livekit.cloud"},
		{"key in the path", "rtmp://ingest.example.test/live/key-6d1a9", "rtmp://ingest.example.test"},
		{"signed pull url", "https://user-4c:pass-8e@cdn.example.test/hls/tok-91/index.m3u8?sig=sig-2f#t=1", "https://cdn.example.test"},
		{"port kept", "srt://ingest.example.test:9000/live?streamid=key-6d1a9", "srt://ingest.example.test:9000"},
		{"unreadable", "://nothing", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ingressURL(tt.url); got != tt.want {
				t.Errorf("ingressURL = %q, want %q", got, tt.want)
			}
		})
	}
}

// Every secret an egress request can carry stays out of the stored projection, even when LiveKit
// repeats one in its error or detail text.
func TestEgressProjectionDropsSecrets(t *testing.T) {
	secrets := []string{
		"s3-access-3e9f", "s3-secret-71ab", "s3-session-0c2d", "s3-external-9a4e", "gcp-credentials-5b8c",
		"azure-account-2d7f", "azure-key-6e1a", "ali-access-8f3b", "ali-secret-4c9d", "proxy-pass-1a2b",
		"stream-key-5f6e", "socket-key-7d8c", "hook-signing-3b4a", "5551234567", "s3-metadata-6c5d", "5559876543",
	}
	in := &livekit.EgressInfo{
		EgressId: "EG_1", RoomName: "call-+15551234567", Status: livekit.EgressStatus_EGRESS_FAILED,
		Request: &livekit.EgressInfo_RoomComposite{RoomComposite: &livekit.RoomCompositeEgressRequest{
			AudioOnly: true,
			FileOutputs: []*livekit.EncodedFileOutput{{
				FileType: livekit.EncodedFileType_OGG,
				Output: &livekit.EncodedFileOutput_S3{S3: &livekit.S3Upload{
					AccessKey: "s3-access-3e9f", Secret: "s3-secret-71ab", SessionToken: "s3-session-0c2d",
					AssumeRoleExternalId: "s3-external-9a4e", Bucket: "bucket", Metadata: map[string]string{"owner": "s3-metadata-6c5d"},
					Proxy: &livekit.ProxyConfig{Url: "http://proxy.example.test", Password: "proxy-pass-1a2b"},
				}},
			}},
			SegmentOutputs: []*livekit.SegmentedFileOutput{{Output: &livekit.SegmentedFileOutput_Gcp{Gcp: &livekit.GCPUpload{Credentials: "gcp-credentials-5b8c"}}}},
			ImageOutputs: []*livekit.ImageOutput{
				{Output: &livekit.ImageOutput_Azure{Azure: &livekit.AzureBlobUpload{AccountName: "azure-account-2d7f", AccountKey: "azure-key-6e1a"}}},
				{Output: &livekit.ImageOutput_AliOSS{AliOSS: &livekit.AliOSSUpload{AccessKey: "ali-access-8f3b", Secret: "ali-secret-4c9d"}}},
			},
			StreamOutputs: []*livekit.StreamOutput{{Urls: []string{"rtmp://live.example.test/app/stream-key-5f6e"}}},
			Webhooks:      []*livekit.WebhookConfig{{Url: "https://hooks.example.test/h", SigningKey: "hook-signing-3b4a"}},
		}},
		StreamResults: []*livekit.StreamInfo{{
			Url: "rtmp://live.example.test/app/stream-key-5f6e", Status: livekit.StreamInfo_FAILED,
			Error: "dial rtmp://live.example.test/app/stream-key-5f6e: refused",
		}},
		FileResults: []*livekit.FileInfo{{Filename: "call-+15551234567.ogg", Location: "https://bucket.s3.example.test/call.ogg?X-Amz-Signature=s3-secret-71ab"}},
		Error:       "upload failed with s3-access-3e9f and gcp-credentials-5b8c; azure-key-6e1a ali-secret-4c9d proxy-pass-1a2b hook-signing-3b4a s3-metadata-6c5d",
		Details:     "End reason: stream-key-5f6e, caller +15559876543",
	}
	// A track egress to a websocket carries its key in the URL.
	track := &livekit.EgressInfo{EgressId: "EG_2", Request: &livekit.EgressInfo_Track{Track: &livekit.TrackEgressRequest{
		Output: &livekit.TrackEgressRequest_WebsocketUrl{WebsocketUrl: "wss://socket.example.test/socket-key-7d8c"},
	}}, Error: "closed wss://socket.example.test/socket-key-7d8c and socket-key-7d8c"}

	// An older server reports its streams only in the deprecated result.
	older := &livekit.EgressInfo{
		EgressId: "EG_4", Status: livekit.EgressStatus_EGRESS_FAILED,
		Result: &livekit.EgressInfo_Stream{Stream: &livekit.StreamInfoList{Info: []*livekit.StreamInfo{{ //nolint:staticcheck // the deprecated result is the case under test
			Url: "rtmp://live.example.test/app/stream-key-5f6e", Status: livekit.StreamInfo_FAILED, Error: "refused for stream-key-5f6e",
		}}}},
		Error: "lost stream-key-5f6e",
	}

	for _, e := range []*livekit.EgressInfo{in, track, older} {
		body, err := json.Marshal(projectEgress(e))
		if err != nil {
			t.Fatal(err)
		}
		for _, s := range secrets {
			if strings.Contains(string(body), s) {
				t.Errorf("%s projection keeps %q: %s", e.GetEgressId(), s, body)
			}
		}
	}
	got := projectEgress(in)
	if len(got.Streams) != 1 || !equal(got.Streams[0].Host, ptr("rtmp://live.example.test")) || got.Streams[0].Status != "FAILED" {
		t.Errorf("streams = %+v, want one failed stream to rtmp://live.example.test", got.Streams)
	}
	req := got.Request
	if req.Kind != "roomComposite" || !equal(req.AudioOnly, ptr(true)) || req.FileType != "OGG" || req.Storage != "s3" ||
		strings.Join(req.Outputs, ",") != "fileOutputs,streamOutputs,segmentOutputs,imageOutputs" {
		t.Errorf("request = %+v", req)
	}
	if got.File == nil || !equal(got.File.BucketHost, ptr("bucket.s3.example.test")) || got.File.SizeBytes == nil {
		t.Errorf("file = %+v, want the bucket host and the size of an ended egress", got.File)
	}
}

// A running egress has not written its file: the size LiveKit reports then is not a size.
func TestEgressUnreportedStaysNull(t *testing.T) {
	got := projectEgress(&livekit.EgressInfo{
		EgressId: "EG_3", Status: livekit.EgressStatus_EGRESS_ACTIVE,
		FileResults: []*livekit.FileInfo{{Filename: "f.ogg"}},
	})
	if got.StartedAt != nil || got.EndedAt != nil || got.Error != nil {
		t.Errorf("times or error = %v %v %v, want null", got.StartedAt, got.EndedAt, got.Error)
	}
	if got.File == nil || got.File.SizeBytes != nil || got.File.DurationS != nil || got.File.BucketHost != nil {
		t.Errorf("file = %+v, want size, duration and bucket host null", got.File)
	}
	if got.Request.Kind != "" || got.Request.AudioOnly != nil || got.Request.Storage != "" || len(got.Request.Outputs) != 0 {
		t.Errorf("request = %+v, want nothing reported", got.Request)
	}
}
