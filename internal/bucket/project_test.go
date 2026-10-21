package bucket

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"testing"
	"time"

	"github.com/minio/minio-go/v7"

	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// A bucket a project brings is stored only when a write and a delete with its key succeed: a wrong
// secret or bucket name is refused, an endpoint with nothing listening or one the address guard
// refuses is unreachable, and neither answer says which.
func TestVerifyBucket(t *testing.T) {
	_, endpoint := startS3(t)
	closed := closedPort(t)
	own := store.ProjectBucket{OrganisationID: "default", ProjectID: "production", Endpoint: endpoint, Bucket: "recordings", AccessKey: "vg-test", SecretKey: "s3-test"}
	with := func(f func(b *store.ProjectBucket)) store.ProjectBucket { b := own; f(&b); return b }
	open := http.DefaultTransport
	guarded := scrape.NewWorkerClient(true).Transport

	for _, tc := range []struct {
		name      string
		b         store.ProjectBucket
		transport http.RoundTripper
		want      error
	}{
		{"right key and secret", own, open, nil},
		{"wrong secret", with(func(b *store.ProjectBucket) { b.SecretKey = "made-up" }), open, store.ErrBucketRefused},
		{"unknown access key", with(func(b *store.ProjectBucket) { b.AccessKey = "nobody" }), open, store.ErrBucketRefused},
		{"no such bucket", with(func(b *store.ProjectBucket) { b.Bucket = "missing-bucket" }), open, store.ErrBucketRefused},
		{"nothing listening", with(func(b *store.ProjectBucket) { b.Endpoint = closed }), open, store.ErrBucketUnreachable},
		{"loopback refused by the guard", own, guarded, store.ErrBucketUnreachable},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := NewVerifier(tc.transport).VerifyBucket(context.Background(), tc.b)
			if tc.want == nil && err != nil || tc.want != nil && !errors.Is(err, tc.want) {
				t.Fatalf("VerifyBucket = %v, want %v", err, tc.want)
			}
		})
	}
}

// Each project's recordings go to the bucket it brought, and every other project's to the
// instance's; retention expires both.
func TestBucketsRouteByProject(t *testing.T) {
	instance, endpoint := startS3(t)
	ctx := context.Background()
	if err := instance.client.MakeBucket(ctx, "own", minio.MakeBucketOptions{}); err != nil {
		t.Fatal(err)
	}
	own := store.ProjectBucket{OrganisationID: "acme", ProjectID: "support", Endpoint: endpoint, Bucket: "own", AccessKey: "vg-test", SecretKey: "s3-test"}
	b := &Buckets{Default: instance, Projects: ownBuckets{own}, Transport: http.DefaultTransport}

	stored := time.Date(2026, 7, 1, 12, 0, 0, 0, time.UTC)
	audio := []byte("OggS recording")
	put := func(org, project string) string {
		t.Helper()
		key, _, err := b.PutRecording(ctx, stored, org, project, "AJ_1", bytes.NewReader(audio), int64(len(audio)))
		if err != nil {
			t.Fatalf("put %s/%s: %v", org, project, err)
		}
		return key
	}
	ownKey, instanceKey := put("acme", "support"), put("default", "production")
	// The owner's own object under the same day prefix is not a recording of this project.
	foreign := "recordings/2026/07/01/owner/backup.ogg"
	if _, err := instance.client.PutObject(ctx, "own", foreign, bytes.NewReader(audio), int64(len(audio)), minio.PutObjectOptions{}); err != nil {
		t.Fatal(err)
	}

	in := func(bucket, key string) bool {
		_, err := instance.client.StatObject(ctx, bucket, key, minio.StatObjectOptions{})
		return err == nil
	}
	if !in("own", ownKey) || in("recordings", ownKey) {
		t.Fatal("the project with its own bucket stored outside it")
	}
	if !in("recordings", instanceKey) || in("own", instanceKey) {
		t.Fatal("the project without a bucket stored outside the instance's")
	}
	media, err := b.Open(ctx, "acme", "support", ownKey)
	if err != nil {
		t.Fatalf("open from its own bucket: %v", err)
	}
	got, _ := io.ReadAll(media)
	_ = media.Close()
	if !bytes.Equal(got, audio) {
		t.Fatalf("read back %q", got)
	}

	removed, err := b.Expire(ctx, stored.AddDate(0, 0, 91))
	if err != nil || removed != 2 || in("own", ownKey) || in("recordings", instanceKey) {
		t.Fatalf("Expire removed %d, %v; want both recordings gone", removed, err)
	}
	if !in("own", foreign) {
		t.Fatal("Expire removed an object of the bucket's owner")
	}
}

// ownBuckets is the buckets projects brought, keyed by nothing: a project not listed has none.
type ownBuckets []store.ProjectBucket

func (o ownBuckets) ProjectBucket(_ context.Context, org, project string) (store.ProjectBucket, error) {
	for _, b := range o {
		if b.OrganisationID == org && b.ProjectID == project {
			return b, nil
		}
	}
	return store.ProjectBucket{}, store.ErrNotFound
}

func (o ownBuckets) ProjectBuckets(context.Context) ([]store.ProjectBucket, error) { return o, nil }

// closedPort is a loopback address with nothing listening.
func closedPort(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	_ = l.Close()
	return addr
}
