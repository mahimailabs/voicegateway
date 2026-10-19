package bucket

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// s3Image is the SeaweedFS image deploy/compose.yaml runs (decision 0026).
const s3Image = "chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"

// A key is the UTC day web stored the recording, then organisation, project and job; a job id
// that is not a plain name is never a path segment.
func TestKey(t *testing.T) {
	at := time.Date(2026, 10, 19, 23, 30, 0, 0, time.FixedZone("EDT", -4*3600))
	got, err := Key(at, "default", "production", "AJ_abc-1")
	if err != nil || got != "recordings/2026/10/20/default/production/AJ_abc-1.ogg" {
		t.Fatalf("Key = %q, %v", got, err)
	}
	for _, job := range []string{"", "../../etc", "AJ/x", strings.Repeat("a", 129)} {
		got, err := Key(at, "default", "production", job)
		name := strings.TrimSuffix(strings.TrimPrefix(got, "recordings/2026/10/20/default/production/"), ".ogg")
		if err != nil || len(name) != 32 || strings.ContainsAny(name, "./") {
			t.Fatalf("Key with job %q = %q, %v; want a random 32-character name", job, got, err)
		}
	}
}

func needDocker(t *testing.T) {
	t.Helper()
	if os.Getenv("CI") != "" {
		return
	}
	if testing.Short() {
		t.Skip("needs Docker; runs in CI")
	}
	testcontainers.SkipIfProviderIsNotHealthy(t)
}

// startBucket runs SeaweedFS's single-node S3 endpoint and waits for it to create its bucket.
func startBucket(t *testing.T) *Bucket {
	t.Helper()
	b, _ := startS3(t)
	return b
}

// startS3 is startBucket that also returns the endpoint, host and port, the bucket is reached at.
func startS3(t *testing.T) (*Bucket, string) {
	t.Helper()
	needDocker(t)
	ctx := context.Background()
	s3, err := testcontainers.Run(ctx, s3Image,
		testcontainers.WithCmd("mini", "-dir=/data", "-ip.bind=0.0.0.0"),
		testcontainers.WithEnv(map[string]string{"AWS_ACCESS_KEY_ID": "vg-test", "AWS_SECRET_ACCESS_KEY": "s3-test", "S3_BUCKET": "recordings"}),
		testcontainers.WithExposedPorts("8333/tcp"),
		testcontainers.WithWaitStrategy(wait.ForListeningPort("8333/tcp").WithStartupTimeout(2*time.Minute)))
	testcontainers.CleanupContainer(t, s3)
	if err != nil {
		t.Fatalf("start s3: %v", err)
	}
	endpoint, err := s3.PortEndpoint(ctx, "8333/tcp", "")
	if err != nil {
		t.Fatal(err)
	}
	b, err := New(config.S3{Endpoint: endpoint, Bucket: "recordings", AccessKey: "vg-test", SecretKey: "s3-test"})
	if err != nil {
		t.Fatal(err)
	}
	for range 30 {
		if ok, err := b.client.BucketExists(ctx, b.name); err == nil && ok {
			return b, endpoint
		}
		time.Sleep(time.Second)
	}
	t.Fatal("bucket was not created")
	return nil, ""
}

// Phase 07a, acceptance checks 2 and 4: a stored recording reads back whole and by range; Expire
// removes every day that ended at or before the cutoff and keeps younger days whole.
func TestRecordingRoundTripAndExpiry(t *testing.T) {
	roundTripAndExpiry(t, startBucket(t))
}

func roundTripAndExpiry(t *testing.T, b *Bucket) {
	t.Helper()
	ctx := context.Background()
	audio := bytes.Repeat([]byte("OggS"), 300_000)
	now := time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)

	put := func(at time.Time, job string, size int64) string {
		t.Helper()
		key, n, err := b.PutRecording(ctx, at, "default", "production", job, bytes.NewReader(audio), size)
		if err != nil || n != int64(len(audio)) {
			t.Fatalf("put %s: %d bytes, %v", job, n, err)
		}
		return key
	}
	old := put(now.Add(-store.RecordingKeep-48*time.Hour), "AJ_old", int64(len(audio)))
	edge := put(now.Add(-store.RecordingKeep+time.Hour), "AJ_edge", -1)
	fresh := put(now, "AJ_fresh", int64(len(audio)))

	media, err := b.Open(ctx, fresh)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := media.Seek(400, io.SeekStart); err != nil {
		t.Fatal(err)
	}
	part := make([]byte, 8)
	if _, err := io.ReadFull(media, part); err != nil || !bytes.Equal(part, audio[400:408]) {
		t.Fatalf("ranged read = %q, %v", part, err)
	}
	_ = media.Close()

	n, err := b.Expire(ctx, now.Add(-store.RecordingKeep))
	if err != nil || n != 1 {
		t.Fatalf("Expire removed %d, %v; want 1", n, err)
	}
	if _, err := b.Open(ctx, old); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("expired recording opens: %v", err)
	}
	for _, key := range []string{edge, fresh} {
		media, err := b.Open(ctx, key)
		if err != nil {
			t.Fatalf("%s was removed: %v", key, err)
		}
		_ = media.Close()
	}
}
